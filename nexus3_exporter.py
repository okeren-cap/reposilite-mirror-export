#!/usr/bin/env python3
import requests
import json
import time
import sys
import argparse
import os
from datetime import datetime

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Nexus to Reposilite Full Export Tool - Synchronize artifacts from Nexus to Reposilite',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s                                           # Use all defaults
  %(prog)s --nexus-repository maven-releases        # Override just the repository
  %(prog)s --reposilite-url http://other:8080       # Override reposilite URL
  %(prog)s --rate-limit 10 --yes --quiet            # Custom rate limit, automated mode
  %(prog)s --nexus-url https://nexus.example.com \\
           --nexus-repository core-releases \\
           --reposilite-url http://localhost:8080 \\
           --reposilite-repository releases          # Full custom configuration
        '''
    )
    
    # Configuration arguments (all optional with defaults)
    parser.add_argument('--nexus-url', 
                       default='http://nexus-deploy.ptsupport:8082',
                       help='Nexus base URL (default: http://nexus-deploy.ptsupport:8082)')
    parser.add_argument('--nexus-repository',
                       default='core-releases', 
                       help='Nexus repository name (default: core-releases)')
    parser.add_argument('--reposilite-url',
                       default='http://de-dustest1.corp.capgemini.com:8090',
                       help='Reposilite base URL (default: http://de-dustest1.corp.capgemini.com:8090)')
    parser.add_argument('--reposilite-repository',
                       default='releases',
                       help='Reposilite repository name (default: releases)')
    
    # Authentication and behavior arguments
    parser.add_argument('--nexus-username', '-u',
                       help='Nexus username for authentication')
    parser.add_argument('--nexus-password', '-p',
                       help='Nexus password (or set NEXUS_PASSWORD env var)')
    parser.add_argument('--rate-limit', '-r', type=int, default=5,
                       help='Rate limit in requests per second (default: 5)')
    parser.add_argument('--yes', '-y', action='store_true',
                       help='Skip confirmation prompt and start immediately')
    parser.add_argument('--quiet', '-q', action='store_true',
                       help='Reduce output verbosity (errors and summary only)')
    parser.add_argument('--log-file', '-l',
                       help='Custom log file path (default: auto-generated)')
    parser.add_argument('--list-repositories', action='store_true',
                       help='List available repositories and exit (no sync performed)')
    parser.add_argument('--test-path',
                       help='Test a single artifact path for debugging (no sync performed)')
    parser.add_argument('--debug', action='store_true',
                       help='Enable detailed debug logging for troubleshooting')
    parser.add_argument('--timeout', type=int, default=60,
                        help='Request timeout in seconds for Nexus API calls (default: 60)')
    
    args = parser.parse_args()
    
    # Handle password from environment variable if not provided
    if args.nexus_username and not args.nexus_password:
        args.nexus_password = os.getenv('NEXUS_PASSWORD')
        if not args.nexus_password:
            # If a username is provided, a password is required.
            # Fail early if not running interactively and no password is provided.
            if not sys.stdout.isatty():
                print("ERROR: Nexus username provided without a password in a non-interactive session.", file=sys.stderr)
                print("Please provide --nexus-password or set NEXUS_PASSWORD environment variable.", file=sys.stderr)
                sys.exit(1)
            import getpass
            args.nexus_password = getpass.getpass('Nexus password: ')
    
    return args

class NexusToReposiliteSyncer:
    def __init__(self, args):
        self.args = args
        self.nexus_session = requests.Session()
        if args.nexus_username and args.nexus_password:
            self.nexus_session.auth = (args.nexus_username, args.nexus_password)
        
        self.reposilite_session = requests.Session()
        
        # Statistics
        self.total_artifacts = 0
        self.success_count = 0
        self.failed_count = 0
        self.failed_paths = []
        self.start_time = datetime.now()
        
        # Create log file
        if args.log_file:
            self.log_file = args.log_file
        else:
            self.log_file = f"nexus-sync-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        
        self.debug_log("Syncer initialized in debug mode.")
        
    def debug_log(self, message):
        """Log a message only if debug mode is enabled."""
        if self.args.debug:
            self.log(f"🔍 DEBUG: {message}", force=True)
            
    def log(self, message, force=False):
        """Log message to console and file"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_message = f"[{timestamp}] {message}"
        
        # Print to console unless in quiet mode (unless forced or it's an error/summary)
        if not self.args.quiet or force or "ERROR" in message or "COMPLETED" in message or "STARTED" in message:
            print(log_message)
        
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(log_message + '\n')
    
    def test_nexus_connectivity(self):
        """Test basic connectivity to Nexus before starting the sync"""
        self.log("Testing Nexus connectivity...")
        status_url = f"{self.args.nexus_url}/service/rest/v1/status"
        self.debug_log(f"Connectivity test URL: {status_url}")
        
        try:
            # Try a simple status endpoint first
            response = self.nexus_session.get(status_url, timeout=self.args.timeout)
            self.debug_log(f"Connectivity test response status: {response.status_code}")
            
            if response.status_code == 200:
                self.log("✓ Nexus server is reachable")
                return True
            elif response.status_code == 401:
                self.log("ERROR: Authentication failed - check username/password", force=True)
                return False
            elif response.status_code == 403:
                self.log("ERROR: Access forbidden - check user permissions", force=True)
                return False
            else:
                self.log(f"WARNING: Nexus status check returned HTTP {response.status_code}", force=True)
                # Continue anyway as some Nexus instances might not have status endpoint
                return True
                
        except requests.exceptions.ConnectionError as e:
            self.log("ERROR: Cannot connect to Nexus server", force=True)
            self.log("Please check:", force=True)
            self.log("  - VPN connection is active", force=True)
            self.log("  - Nexus URL is correct", force=True)
            self.log("  - Network connectivity", force=True)
            self.log(f"  - Technical details: {e}", force=True)
            return False
        except requests.exceptions.Timeout:
            self.log("ERROR: Connection to Nexus timed out", force=True)
            self.log("Please check your network connection and VPN status", force=True)
            return False
        except Exception as e:
            self.log(f"ERROR: Connectivity test failed: {e}", force=True)
            return False

    def list_nexus_repositories(self):
        """List all available repositories in Nexus"""
        self.log("Fetching available repositories from Nexus...")
        
        url = f"{self.args.nexus_url}/service/rest/v1/repositories"
        self.debug_log(f"Repository list URL: {url}")
        
        try:
            response = self.nexus_session.get(url, timeout=self.args.timeout)
            self.debug_log(f"Repository list response status: {response.status_code}")
            
            if response.status_code == 200:
                repositories = response.json()
                self.debug_log(f"Found {len(repositories)} repositories in total.")
                
                self.log("Available repositories in Nexus:", force=True)
                self.log("=" * 50, force=True)
                
                maven_repos = []
                other_repos = []
                
                for repo in repositories:
                    repo_name = repo.get('name', 'Unknown')
                    repo_format = repo.get('format', 'Unknown')
                    repo_type = repo.get('type', 'Unknown')
                    
                    if repo_format.lower() == 'maven2':
                        maven_repos.append((repo_name, repo_type))
                    else:
                        other_repos.append((repo_name, repo_format, repo_type))
                
                if maven_repos:
                    self.log("Maven2 repositories (compatible with this tool):", force=True)
                    for name, repo_type in sorted(maven_repos):
                        self.log(f"  {name} ({repo_type})", force=True)
                
                if other_repos:
                    self.log("\nOther repository formats:", force=True)
                    for name, format_type, repo_type in sorted(other_repos):
                        self.log(f"  {name} ({format_type}, {repo_type})", force=True)
                
                self.log("=" * 50, force=True)
                self.log(f"Total repositories: {len(repositories)}", force=True)
                self.log(f"Maven2 repositories: {len(maven_repos)}", force=True)
                
                return True
                
            elif response.status_code == 401:
                self.log("ERROR: Authentication failed - check username/password", force=True)
                return False
            elif response.status_code == 403:
                self.log("ERROR: Access forbidden - check user permissions", force=True)
                return False
            else:
                self.log(f"ERROR: HTTP {response.status_code} - {response.reason}", force=True)
                return False
                
        except requests.exceptions.ConnectionError as e:
            self.log("ERROR: Cannot connect to Nexus server", force=True)
            self.log(f"Technical details: {e}", force=True)
            return False
        except requests.exceptions.Timeout:
            self.log("ERROR: Request to Nexus timed out", force=True)
            return False
        except Exception as e:
            self.log(f"ERROR: Failed to list repositories: {e}", force=True)
            return False

    def get_all_asset_paths_from_nexus(self):
        """Get all asset paths from Nexus by listing components and their assets."""
        self.log(f"Fetching all components from Nexus repository: {self.args.nexus_repository}")
        self.log(f"Nexus URL: {self.args.nexus_url}")
        self.log(f"Authentication: {self.args.nexus_username}:{'*' * len(self.args.nexus_password) if self.args.nexus_password else 'None'}")

        asset_paths = []
        continuation_token = None
        page = 1
        component_count = 0

        while True:
            params = {
                'repository': self.args.nexus_repository
            }
            if continuation_token:
                params['continuationToken'] = continuation_token

            url = f"{self.args.nexus_url}/service/rest/v1/components"

            try:
                self.log(f"Fetching page {page} of components...")
                self.debug_log(f"Component fetch URL: {url}")
                self.debug_log(f"Component fetch params: {params}")

                response = self.nexus_session.get(url, params=params, timeout=self.args.timeout)

                self.debug_log(f"Component fetch response status: {response.status_code}")
                self.debug_log(f"Component fetch response headers: {dict(response.headers)}")
                
                if response.status_code != 200:
                    self.log(f"ERROR: HTTP {response.status_code} - {response.reason}", force=True)
                    self.log(f"Response content: {response.text[:500]}...", force=True)
                    break

                data = response.json()
                self.debug_log(f"Response JSON keys: {list(data.keys())}")
                self.debug_log(f"Continuation token from response: {data.get('continuationToken')}")
                page_components = data.get('items', [])
                
                component_count += len(page_components)
                assets_on_page = 0
                
                for component in page_components:
                    for asset in component.get('assets', []):
                        if asset.get('path'):
                            asset_paths.append(asset['path'])
                            assets_on_page += 1

                self.log(f"Page {page}: Found {len(page_components)} components with {assets_on_page} assets (Total assets: {len(asset_paths)})")

                if page_components:
                    self.debug_log(f"Sample component from page: {json.dumps(page_components[0], indent=2)}")
                else:
                    self.debug_log("No components found on this page.")

                continuation_token = data.get('continuationToken')
                if not continuation_token:
                    break
                
                page += 1
                time.sleep(0.2)

            except requests.exceptions.ConnectionError as e:
                self.log("ERROR: Connection lost to Nexus server during component fetch.", force=True)
                self.log(f"Technical details: {e}", force=True)
                break
            except requests.exceptions.Timeout:
                self.log("ERROR: Request to Nexus for components timed out.", force=True)
                break
            except requests.RequestException as e:
                self.log(f"ERROR: Failed to fetch components from Nexus: {e}", force=True)
                break
            except json.JSONDecodeError:
                self.log(f"ERROR: Invalid JSON response from Nexus component API.", force=True)
                self.log(f"Response content: {response.text[:500]}...", force=True)
                break

        self.log(f"Total components found: {component_count}")
        self.log(f"Total asset paths found in Nexus: {len(asset_paths)}")
        return asset_paths
    
    def request_artifact_in_reposilite(self, path):
        """Request artifact in Reposilite to trigger caching"""
        url = f"{self.args.reposilite_url}/{self.args.reposilite_repository}/{path}"
        self.debug_log(f"Sending HEAD request to Reposilite: {url}")
        
        try:
            # Use HEAD request to trigger caching without downloading full content
            response = self.reposilite_session.head(url, timeout=self.args.timeout)
            self.debug_log(f"Reposilite response status for '{path}': {response.status_code}")
            
            if response.status_code == 200:
                return True, "Success"
            elif response.status_code == 404:
                return False, "Not found (may not exist in Nexus mirror)"
            elif response.status_code == 401:
                return False, "Authentication required"
            elif response.status_code == 403:
                return False, "Access forbidden"
            else:
                return False, f"HTTP {response.status_code}"
                
        except requests.Timeout:
            return False, "Timeout"
        except requests.RequestException as e:
            return False, f"Request error: {str(e)}"
    
    def sync_all_artifacts(self):
        """Main synchronization process"""
        self.log("=" * 80, force=True)
        self.log("NEXUS TO REPOSILITE FULL EXPORT STARTED", force=True)
        self.log("=" * 80, force=True)
        self.log(f"Source: {self.args.nexus_url}/repository/{self.args.nexus_repository}", force=True)
        self.log(f"Target: {self.args.reposilite_url}/{self.args.reposilite_repository}", force=True)
        self.log(f"Log file: {self.log_file}", force=True)
        
        # Step 0: Test connectivity first
        if not self.test_nexus_connectivity():
            self.log("ERROR: Cannot establish connection to Nexus - aborting sync", force=True)
            return False
        
        # Step 1: Get all asset paths from Nexus
        asset_paths = self.get_all_asset_paths_from_nexus()
        self.debug_log(f"Total asset paths retrieved from Nexus: {len(asset_paths)}")
        
        if not asset_paths:
            self.log("ERROR: No asset paths found or failed to fetch from Nexus", force=True)
            self.log("TROUBLESHOOTING:", force=True)
            self.log("1. Check if the repository name is correct with --list-repositories", force=True)
            self.log("2. Verify read permissions for the user on the repository.", force=True)
            self.log("3. The repository might actually be empty.", force=True)
            return False
        
        # Step 2: Process each artifact path
        self.log("\n" + "=" * 80, force=True)
        self.log("STARTING ARTIFACT SYNCHRONIZATION", force=True)
        self.log("=" * 80, force=True)
        
        total_paths = len(asset_paths)
        for i, path in enumerate(asset_paths, 1):
            self.debug_log(f"Processing path {i}/{total_paths}: {path}")
            self.log(f"\n[{i}/{total_paths}] Requesting: {path}")
            
            success, message = self.request_artifact_in_reposilite(path)
            
            if success:
                self.log(f"  ✓ SUCCESS: {path}")
                self.success_count += 1
            else:
                self.log(f"  ✗ FAILED: {path} ({message})")
                self.failed_count += 1
                self.failed_paths.append((path, message))
            
            self.total_artifacts += 1
            
            # Rate limiting
            time.sleep(1.0 / self.args.rate_limit)
            
            # Progress update every 50 artifacts
            if self.total_artifacts % 50 == 0 and self.total_artifacts > 0:
                elapsed = datetime.now() - self.start_time
                if elapsed.total_seconds() > 0:
                    rate = self.total_artifacts / elapsed.total_seconds()
                    self.log(f"  Progress: {self.total_artifacts} artifacts processed, {rate:.2f} artifacts/sec")
        
        # Final summary
        self.print_summary()
        return True
    
    def print_summary(self):
        """Print final synchronization summary"""
        elapsed = datetime.now() - self.start_time
        rate = self.total_artifacts / elapsed.total_seconds() if elapsed.total_seconds() > 0 else 0
        
        self.log("\n" + "=" * 80, force=True)
        self.log("SYNCHRONIZATION COMPLETED", force=True)
        self.log("=" * 80, force=True)
        self.log(f"Total artifacts processed: {self.total_artifacts}", force=True)
        self.log(f"Successfully cached: {self.success_count}", force=True)
        self.log(f"Failed: {self.failed_count}", force=True)
        
        if self.failed_paths:
            self.log("--- FAILED ARTIFACTS ---", force=True)
            # Limit printing to avoid flooding console, full list is in log
            for i, (path, reason) in enumerate(self.failed_paths):
                if i < 20: # Show first 20 failures in summary
                    self.log(f"  - {path} (Reason: {reason})", force=True)
            if len(self.failed_paths) > 20:
                self.log(f"  ... and {len(self.failed_paths) - 20} more. See log file for full list.", force=True)
            self.log("------------------------", force=True)

        self.log(f"Success rate: {(self.success_count/self.total_artifacts*100):.1f}%" if self.total_artifacts > 0 else "N/A", force=True)
        self.log(f"Total time: {str(elapsed).split('.')[0]}", force=True)
        self.log(f"Average rate: {rate:.2f} artifacts/second", force=True)
        self.log(f"Log file: {self.log_file}", force=True)
        self.log("=" * 80, force=True)

    def test_single_path(self, path):
        """Test a single path against Reposilite with detailed debug output"""
        self.log("--- STARTING SINGLE PATH TEST ---", force=True)
        self.log(f"Testing path: {path}", force=True)
        
        url = f"{self.args.reposilite_url}/{self.args.reposilite_repository}/{path}"
        self.log(f"Request URL: {url}", force=True)
        
        self.log("\n--- Request ---", force=True)
        self.log("Method: GET (to retrieve error details if any)", force=True)

        try:
            # Use GET with stream=True to get headers and potentially body without downloading a large file
            response = self.reposilite_session.get(url, timeout=self.args.timeout, stream=True)
            
            self.log("\n--- Response ---", force=True)
            self.log(f"Status Code: {response.status_code} {response.reason}", force=True)
            
            self.log("\n--- Response Headers ---", force=True)
            for header, value in response.headers.items():
                self.log(f"  {header}: {value}", force=True)

            if response.status_code == 200:
                self.log("\n--- Result ---", force=True)
                self.log("✓ SUCCESS: Artifact is available in Reposilite.", force=True)
                self.log(f"Content-Length: {response.headers.get('Content-Length', 'N/A')}", force=True)
            else:
                self.log("\n--- Response Body (first 500 bytes) ---", force=True)
                body = response.raw.read(500)
                try:
                    self.log(body.decode('utf-8'), force=True)
                except UnicodeDecodeError:
                    self.log(str(body), force=True)

                self.log("\n--- Result ---", force=True)
                self.log("✗ FAILED: Artifact not available or an error occurred.", force=True)
                self.log("\n--- TROUBLESHOOTING SUGGESTIONS ---", force=True)
                self.log("1. Check Reposilite server logs for errors related to the request.", force=True)
                self.log(f"   (Look for requests to {self.args.reposilite_repository}/{path})", force=True)
                self.log("2. Verify that the Reposilite server can connect to the Nexus server:", force=True)
                self.log(f"   - Nexus URL configured in Reposilite should be reachable from Reposilite's host.", force=True)
                self.log("3. If Nexus requires authentication, ensure credentials are correctly configured in Reposilite's settings for the remote repository.", force=True)
                self.log("4. The artifact might truly be missing from the mirrored Nexus repo, despite being listed.", force=True)

        except requests.exceptions.Timeout:
            self.log("\n--- Result ---", force=True)
            self.log("✗ FAILED: Request to Reposilite timed out.", force=True)
        except requests.exceptions.RequestException as e:
            self.log("\n--- Result ---", force=True)
            self.log(f"✗ FAILED: A request error occurred: {e}", force=True)

        self.log("\n--- END OF SINGLE PATH TEST ---", force=True)

def main():
    args = parse_arguments()
    syncer = NexusToReposiliteSyncer(args)

    # Handle single path test mode
    if args.test_path:
        print("--- Single Path Test Mode ---")
        syncer.test_single_path(args.test_path)
        sys.exit(0)
    
    # Handle repository listing mode
    if args.list_repositories:
        print("Nexus Repository Discovery Tool")
        print("=" * 50)
        print(f"Nexus URL: {args.nexus_url}")
        if args.nexus_username:
            print(f"Authentication: {args.nexus_username}")
        print()
        
        # Create syncer just for repository listing
        success = syncer.list_nexus_repositories()
        
        if success:
            print("\nTo sync artifacts from a specific repository, use:")
            print(f"python3 {sys.argv[0]} --nexus-repository <repository-name>")
        
        sys.exit(0 if success else 1)
    
    print("Nexus to Reposilite Full Export Tool")
    print("=" * 50)
    
    # Display configuration
    print(f"Source: {args.nexus_url}/repository/{args.nexus_repository}")
    print(f"Target: {args.reposilite_url}/{args.reposilite_repository}")
    print(f"Rate limit: {args.rate_limit} requests/second")
    print(f"Timeout: {args.timeout} seconds")
    if args.nexus_username:
        print(f"Nexus authentication: {args.nexus_username}")
    print()
    
    # Show helpful hints
    print("💡 TIP: Use --list-repositories to see available repositories first")
    if args.debug:
        print("🔍 DEBUG MODE: Verbose logging is active.")
    print()
    
    # Confirm before starting (unless --yes flag is used)
    if not args.yes:
        response = input("Do you want to start the full export? This may take a long time! (y/N): ")
        if response.lower() != 'y':
            print("Export cancelled.")
            print("\nTo discover available repositories, run:")
            print(f"python3 {sys.argv[0]} --list-repositories")
            sys.exit(0)
    
    # Start synchronization
    try:
        success = syncer.sync_all_artifacts()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        syncer.log("\nERROR: Export interrupted by user", force=True)
        syncer.print_summary()
        sys.exit(1)
    except Exception as e:
        syncer.log(f"ERROR: Unexpected error: {e}", force=True)
        syncer.print_summary()
        sys.exit(1)

if __name__ == "__main__":
    main()