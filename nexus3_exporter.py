#!/usr/bin/env python3
import requests
import json
import time
import sys
import argparse
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Nexus Export Tool - Synchronize artifacts from Nexus to Reposilite or export directly to filesystem',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Reposilite Caching (Original Functionality):
  %(prog)s                                           # Use all defaults with both APIs for max completeness
  %(prog)s --nexus-repository maven-releases        # Override just the repository
  %(prog)s --reposilite-url http://other:8080       # Override reposilite URL
  %(prog)s --rate-limit 10 --yes --quiet            # Custom rate limit, automated mode
  %(prog)s --validate-after-sync                    # Sync and then validate completeness
  %(prog)s --validate-only                          # Only validate existing sync without re-syncing

  # Filesystem Export (NEW Feature using downloadUrls):
  %(prog)s --export-to-path /tmp/maven-export       # Export to filesystem only
  %(prog)s --export-to-path ./artifacts \
           --export-parallel-downloads 10 \
           --yes                                     # Fast parallel export
  %(prog)s --export-to-path /backup/maven \
           --reposilite-url http://localhost:8080   # Export AND cache in Reposilite
  %(prog)s --export-only \
           --export-to-path /export/maven           # Export only, skip Reposilite entirely

  # API Selection (works with both modes):
  %(prog)s --use-assets-api-only                    # Use Assets API only
  %(prog)s --use-components-api-only                # Use Components API only
  %(prog)s --nexus-url https://nexus.example.com \
           --nexus-repository core-releases \
           --export-to-path /mnt/artifacts \
           --export-parallel-downloads 15           # High-performance export
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
    parser.add_argument('--debug', action='store_true',
                       help='Enable detailed debug logging for troubleshooting')
    parser.add_argument('--timeout', type=int, default=60,
                        help='Request timeout in seconds for Nexus API calls (default: 60)')
    parser.add_argument('--list-gavs', action='store_true',
                        help='List all unique GAVs (groupId:artifactId:version) in the specified repository and exit')
    parser.add_argument('--gavs-output',
                        help='Optional file to write GAVs. If ends with .json, a JSON array of objects will be written; otherwise one GAV per line')
    parser.add_argument('--sync-by-gav', action='store_true',
                        help='Sync all assets by first discovering GAVs and then fetching all files for each GAV.')
    
    parser.add_argument('--generate-tree-view', action='store_true',
                        help='Generates a file tree view of all artifacts in the repository and exits.')
    parser.add_argument('--tree-output-file',
                        help='Optional file to write the tree view to. If not provided, prints to console.')
    
    parser.add_argument('--use-assets-api-only', action='store_true',
                        help='Use only the Assets API for fetching artifacts (legacy behavior)')
    parser.add_argument('--use-components-api-only', action='store_true',
                        help='Use only the Components API for fetching artifacts')
    # If neither flag is specified, both APIs will be used for maximum completeness
    
    parser.add_argument('--validate-after-sync', action='store_true',
                        help='Validate that all assets are available in Reposilite after sync completes')
    parser.add_argument('--validate-only', action='store_true',
                        help='Skip sync and only validate existing assets in Reposilite against Nexus')
    
    # Export feature arguments
    parser.add_argument('--export-to-path', 
                       help='Export artifacts directly to local filesystem instead of caching in Reposilite')
    parser.add_argument('--preserve-structure', action='store_true', default=True,
                       help='Maintain Maven directory structure in export (default: True)')
    parser.add_argument('--export-parallel-downloads', type=int, default=5,
                       help='Number of parallel downloads for export (default: 5)')
    parser.add_argument('--export-only', action='store_true',
                       help='Only export to filesystem, skip Reposilite caching entirely')
    
    args = parser.parse_args()
    
    # Handle password from environment variable if not provided
    if args.nexus_username and not args.nexus_password:
        args.nexus_password = os.getenv('NEXUS_PASSWORD')
        if not args.nexus_password:
            import getpass
            args.nexus_password = getpass.getpass('Nexus password: ')
    
    # Validate export arguments
    if args.export_to_path:
        # Create export directory if it doesn't exist
        export_path = Path(args.export_to_path)
        try:
            export_path.mkdir(parents=True, exist_ok=True)
            if not export_path.is_dir():
                parser.error(f"Export path '{args.export_to_path}' is not a directory")
        except PermissionError:
            parser.error(f"Permission denied creating export directory '{args.export_to_path}'")
        except Exception as e:
            parser.error(f"Invalid export path '{args.export_to_path}': {e}")
    
    # Validate export-only mode
    if args.export_only and not args.export_to_path:
        parser.error("--export-only requires --export-to-path to be specified")
    
    # Validate parallel downloads
    if args.export_parallel_downloads < 1 or args.export_parallel_downloads > 20:
        parser.error("--export-parallel-downloads must be between 1 and 20")
    
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
        """Get all asset paths from Nexus using the Search Assets API."""
        self.log(f"Fetching all asset paths from Nexus repository: {self.args.nexus_repository}")
        self.log(f"Nexus URL: {self.args.nexus_url}")
        self.log(f"Authentication: {self.args.nexus_username}:{'*' * len(self.args.nexus_password) if self.args.nexus_password else 'None'}")

        asset_paths = []
        continuation_token = None
        page = 1

        while True:
            params = {
                'repository': self.args.nexus_repository
            }
            if continuation_token:
                params['continuationToken'] = continuation_token

            url = f"{self.args.nexus_url}/service/rest/v1/search/assets"

            try:
                self.log(f"Fetching page {page} of assets...")
                self.debug_log(f"Asset fetch URL: {url}")
                self.debug_log(f"Asset fetch params: {params}")

                response = self.nexus_session.get(url, params=params, timeout=self.args.timeout)

                self.debug_log(f"Asset fetch response status: {response.status_code}")
                self.debug_log(f"Asset fetch response headers: {dict(response.headers)}")
                
                if response.status_code != 200:
                    self.log(f"ERROR: HTTP {response.status_code} - {response.reason}", force=True)
                    self.log(f"Response content: {response.text[:500]}...", force=True)
                    break

                data = response.json()
                self.debug_log(f"Response JSON keys: {list(data.keys())}")
                self.debug_log(f"Continuation token from response: {data.get('continuationToken')}")
                page_items = data.get('items', [])
                
                for asset in page_items:
                    if asset.get('path'):
                        asset_paths.append(asset['path'])

                self.log(f"Page {page}: Found {len(page_items)} assets (Total: {len(asset_paths)})")

                if page_items:
                    self.debug_log(f"Sample asset path from page: {page_items[0].get('path', 'N/A')}")
                else:
                    self.debug_log("No assets found on this page.")

                continuation_token = data.get('continuationToken')
                if not continuation_token:
                    break
                
                page += 1
                time.sleep(0.2)

            except requests.exceptions.ConnectionError as e:
                self.log("ERROR: Connection lost to Nexus server during asset fetch.", force=True)
                self.log(f"Technical details: {e}", force=True)
                break
            except requests.exceptions.Timeout:
                self.log("ERROR: Request to Nexus for assets timed out.", force=True)
                break
            except requests.RequestException as e:
                self.log(f"ERROR: Failed to fetch assets from Nexus: {e}", force=True)
                break
            except json.JSONDecodeError:
                self.log(f"ERROR: Invalid JSON response from Nexus asset API.", force=True)
                self.log(f"Response content: {response.text[:500]}...", force=True)
                break

        self.log(f"Total asset paths found in Nexus: {len(asset_paths)}")
        return asset_paths
    
    def get_all_asset_paths_from_components_api(self):
        """Get all asset paths from Nexus using the Components API as an alternative method."""
        self.log(f"Fetching all asset paths from Nexus Components API for repository: {self.args.nexus_repository}")

        asset_paths = []
        continuation_token = None
        page = 1

        while True:
            params = {
                'repository': self.args.nexus_repository
            }
            if continuation_token:
                params['continuationToken'] = continuation_token

            url = f"{self.args.nexus_url}/service/rest/v1/components"

            try:
                self.log(f"Fetching page {page} of components...")
                self.debug_log(f"Components fetch URL: {url}")
                self.debug_log(f"Components fetch params: {params}")

                response = self.nexus_session.get(url, params=params, timeout=self.args.timeout)

                self.debug_log(f"Components fetch response status: {response.status_code}")
                
                if response.status_code != 200:
                    self.log(f"ERROR: HTTP {response.status_code} - {response.reason}", force=True)
                    self.log(f"Response content: {response.text[:500]}...", force=True)
                    break

                data = response.json()
                page_items = data.get('items', [])
                
                for component in page_items:
                    # Each component has multiple assets
                    for asset in component.get('assets', []):
                        if asset.get('path'):
                            asset_paths.append(asset['path'])

                self.log(f"Page {page}: Found {len(page_items)} components with {len([a for c in page_items for a in c.get('assets', [])])} assets (Total asset paths: {len(asset_paths)})")

                continuation_token = data.get('continuationToken')
                if not continuation_token:
                    break
                
                page += 1
                time.sleep(0.2)

            except requests.exceptions.ConnectionError as e:
                self.log("ERROR: Connection lost to Nexus server during components fetch.", force=True)
                self.log(f"Technical details: {e}", force=True)
                break
            except requests.exceptions.Timeout:
                self.log("ERROR: Request to Nexus for components timed out.", force=True)
                break
            except requests.RequestException as e:
                self.log(f"ERROR: Failed to fetch components from Nexus: {e}", force=True)
                break
            except json.JSONDecodeError:
                self.log(f"ERROR: Invalid JSON response from Nexus components API.", force=True)
                self.log(f"Response content: {response.text[:500]}...", force=True)
                break

        self.log(f"Total asset paths found via Components API: {len(asset_paths)}")
        return asset_paths
    
    def get_all_download_info_from_assets_api(self):
        """Get all asset download info from Nexus using the Search Assets API - enhanced for export feature."""
        self.log(f"Fetching all asset download info from Nexus repository: {self.args.nexus_repository}")
        self.log(f"Nexus URL: {self.args.nexus_url}")
        self.log(f"Authentication: {self.args.nexus_username}:{'*' * len(self.args.nexus_password) if self.args.nexus_password else 'None'}")

        download_info = []
        continuation_token = None
        page = 1

        while True:
            params = {
                'repository': self.args.nexus_repository
            }
            if continuation_token:
                params['continuationToken'] = continuation_token

            url = f"{self.args.nexus_url}/service/rest/v1/search/assets"

            try:
                self.log(f"Fetching page {page} of assets...")
                self.debug_log(f"Asset fetch URL: {url}")
                self.debug_log(f"Asset fetch params: {params}")

                response = self.nexus_session.get(url, params=params, timeout=self.args.timeout)

                self.debug_log(f"Asset fetch response status: {response.status_code}")
                
                if response.status_code != 200:
                    self.log(f"ERROR: HTTP {response.status_code} - {response.reason}", force=True)
                    self.log(f"Response content: {response.text[:500]}...", force=True)
                    break

                data = response.json()
                page_items = data.get('items', [])
                
                for asset in page_items:
                    if asset.get('path') and asset.get('downloadUrl'):
                        info = {
                            'path': asset['path'],
                            'downloadUrl': asset['downloadUrl'],
                            'contentType': asset.get('contentType', ''),
                            'size': asset.get('contentLength', 0),
                            'checksum': asset.get('checksum', {}),
                            'format': asset.get('format', ''),
                            'repository': asset.get('repository', self.args.nexus_repository)
                        }
                        download_info.append(info)

                self.log(f"Page {page}: Found {len(page_items)} assets (Total: {len(download_info)})")

                continuation_token = data.get('continuationToken')
                if not continuation_token:
                    break
                
                page += 1
                time.sleep(0.2)

            except requests.exceptions.ConnectionError as e:
                self.log("ERROR: Connection lost to Nexus server during asset fetch.", force=True)
                self.log(f"Technical details: {e}", force=True)
                break
            except requests.exceptions.Timeout:
                self.log("ERROR: Request to Nexus for assets timed out.", force=True)
                break
            except requests.RequestException as e:
                self.log(f"ERROR: Failed to fetch assets from Nexus: {e}", force=True)
                break
            except json.JSONDecodeError:
                self.log(f"ERROR: Invalid JSON response from Nexus asset API.", force=True)
                self.log(f"Response content: {response.text[:500]}...", force=True)
                break

        self.log(f"Total asset download info found in Nexus: {len(download_info)}")
        return download_info
    
    def get_all_download_info_from_components_api(self):
        """Get all asset download info from Nexus using the Components API - enhanced for export feature."""
        self.log(f"Fetching all asset download info from Nexus Components API for repository: {self.args.nexus_repository}")

        download_info = []
        continuation_token = None
        page = 1

        while True:
            params = {
                'repository': self.args.nexus_repository
            }
            if continuation_token:
                params['continuationToken'] = continuation_token

            url = f"{self.args.nexus_url}/service/rest/v1/components"

            try:
                self.log(f"Fetching page {page} of components...")
                self.debug_log(f"Components fetch URL: {url}")
                self.debug_log(f"Components fetch params: {params}")

                response = self.nexus_session.get(url, params=params, timeout=self.args.timeout)

                self.debug_log(f"Components fetch response status: {response.status_code}")
                
                if response.status_code != 200:
                    self.log(f"ERROR: HTTP {response.status_code} - {response.reason}", force=True)
                    self.log(f"Response content: {response.text[:500]}...", force=True)
                    break

                data = response.json()
                page_items = data.get('items', [])
                
                for component in page_items:
                    # Each component has multiple assets
                    for asset in component.get('assets', []):
                        if asset.get('path') and asset.get('downloadUrl'):
                            info = {
                                'path': asset['path'],
                                'downloadUrl': asset['downloadUrl'],
                                'contentType': asset.get('contentType', ''),
                                'size': asset.get('contentLength', 0),
                                'checksum': asset.get('checksum', {}),
                                'format': asset.get('format', ''),
                                'repository': asset.get('repository', self.args.nexus_repository),
                                # Add component info for better tracking
                                'component': {
                                    'group': component.get('group', ''),
                                    'name': component.get('name', ''),
                                    'version': component.get('version', '')
                                }
                            }
                            download_info.append(info)

                self.log(f"Page {page}: Found {len(page_items)} components with {len([a for c in page_items for a in c.get('assets', [])])} assets (Total download info: {len(download_info)})")

                continuation_token = data.get('continuationToken')
                if not continuation_token:
                    break
                
                page += 1
                time.sleep(0.2)

            except requests.exceptions.ConnectionError as e:
                self.log("ERROR: Connection lost to Nexus server during components fetch.", force=True)
                self.log(f"Technical details: {e}", force=True)
                break
            except requests.exceptions.Timeout:
                self.log("ERROR: Request to Nexus for components timed out.", force=True)
                break
            except requests.RequestException as e:
                self.log(f"ERROR: Failed to fetch components from Nexus: {e}", force=True)
                break
            except json.JSONDecodeError:
                self.log(f"ERROR: Invalid JSON response from Nexus components API.", force=True)
                self.log(f"Response content: {response.text[:500]}...", force=True)
                break

        self.log(f"Total asset download info found via Components API: {len(download_info)}")
        return download_info
    
    def get_all_gavs_from_nexus(self):
        """Fetch every asset via the Assets Search API and collect unique GAVs (groupId, artifactId, version)."""
        self.log(f"Fetching all unique GAVs from Nexus repository: {self.args.nexus_repository}")
        self.log(f"Nexus URL: {self.args.nexus_url}")
        self.log(f"Authentication: {self.args.nexus_username}:{'*' * len(self.args.nexus_password) if self.args.nexus_password else 'None'}")

        unique_gavs = set()
        continuation_token = None
        page = 1

        while True:
            params = {
                'repository': self.args.nexus_repository
            }
            if continuation_token:
                params['continuationToken'] = continuation_token

            url = f"{self.args.nexus_url}/service/rest/v1/search/assets"

            try:
                self.log(f"Fetching page {page} of assets...")
                self.debug_log(f"Asset fetch URL: {url}")
                self.debug_log(f"Asset fetch params: {params}")

                response = self.nexus_session.get(url, params=params, timeout=self.args.timeout)

                self.debug_log(f"Asset fetch response status: {response.status_code}")
                self.debug_log(f"Asset fetch response headers: {dict(response.headers)}")

                if response.status_code != 200:
                    self.log(f"ERROR: HTTP {response.status_code} - {response.reason}", force=True)
                    self.log(f"Response content: {response.text[:500]}...", force=True)
                    break

                data = response.json()
                page_items = data.get('items', [])

                for asset in page_items:
                    if str(asset.get('format', '')).lower() != 'maven2':
                        continue

                    m2 = asset.get('maven2') or {}
                    group_id = m2.get('groupId')
                    artifact_id = m2.get('artifactId')
                    version = m2.get('version')

                    if group_id and artifact_id and version:
                        unique_gavs.add((group_id, artifact_id, version))
                        continue

                    # Fallback to parsing path for safety
                    path = asset.get('path') or ''
                    if not path:
                        continue
                    # Include all files including metadata and checksums as they are essential for Maven repositories

                    segments = path.split('/')
                    # Expect .../<groupId as path>/<artifactId>/<version>/<file>
                    if len(segments) >= 4:
                        parsed_version = segments[-2]
                        parsed_artifact_id = segments[-3]
                        group_parts = segments[:-3]
                        parsed_group_id = '.'.join(group_parts) if group_parts else None
                        if parsed_group_id and parsed_artifact_id and parsed_version:
                            unique_gavs.add((parsed_group_id, parsed_artifact_id, parsed_version))

                self.log(f"Page {page}: Processed {len(page_items)} assets (Unique GAVs so far: {len(unique_gavs)})")

                continuation_token = data.get('continuationToken')
                if not continuation_token:
                    break

                page += 1
                time.sleep(0.2)

            except requests.exceptions.ConnectionError as e:
                self.log("ERROR: Connection lost to Nexus server during assets scan.", force=True)
                self.log(f"Technical details: {e}", force=True)
                break
            except requests.exceptions.Timeout:
                self.log("ERROR: Request to Nexus for assets timed out.", force=True)
                break
            except requests.RequestException as e:
                self.log(f"ERROR: Failed to fetch assets from Nexus: {e}", force=True)
                break
            except json.JSONDecodeError:
                self.log(f"ERROR: Invalid JSON response from Nexus asset API.", force=True)
                self.log(f"Response content: {response.text[:500]}...", force=True)
                break

        self.log(f"Total unique GAVs found: {len(unique_gavs)}")
        return unique_gavs
    
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
    
    def download_single_asset(self, asset_info, export_base_path):
        """Download a single asset to the filesystem using its downloadUrl"""
        path = asset_info['path']
        download_url = asset_info['downloadUrl']
        
        try:
            # Determine target file path
            if self.args.preserve_structure:
                target_file_path = Path(export_base_path) / path
            else:
                # Flat structure - just use filename
                filename = os.path.basename(path)
                target_file_path = Path(export_base_path) / filename
            
            # Create directory structure if needed
            target_file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Skip if file already exists and has same size
            if target_file_path.exists():
                existing_size = target_file_path.stat().st_size
                expected_size = asset_info.get('size', 0)
                if existing_size == expected_size and expected_size > 0:
                    self.debug_log(f"Skipping {path} - already exists with correct size")
                    return True, "Already exists", target_file_path
            
            # Download the file
            self.debug_log(f"Downloading {download_url} to {target_file_path}")
            response = self.nexus_session.get(download_url, stream=True, timeout=self.args.timeout)
            
            if response.status_code == 200:
                with open(target_file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                # Verify file size if available
                expected_size = asset_info.get('size', 0)
                actual_size = target_file_path.stat().st_size
                if expected_size > 0 and actual_size != expected_size:
                    return False, f"Size mismatch: expected {expected_size}, got {actual_size}", target_file_path
                
                return True, "Downloaded", target_file_path
            else:
                return False, f"HTTP {response.status_code}: {response.reason}", target_file_path
                
        except Exception as e:
            return False, f"Download error: {str(e)}", None
    
    def export_artifacts_to_filesystem(self, download_info):
        """Export all artifacts to filesystem using parallel downloads"""
        export_path = self.args.export_to_path
        self.log(f"Starting export to: {export_path}", force=True)
        self.log(f"Export mode: {'Preserve directory structure' if self.args.preserve_structure else 'Flat structure'}", force=True)
        self.log(f"Parallel downloads: {self.args.export_parallel_downloads}", force=True)
        self.log(f"Total assets to export: {len(download_info)}", force=True)
        
        # Export statistics
        export_stats = {
            'total': len(download_info),
            'success': 0,
            'failed': 0,
            'already_exists': 0,
            'total_bytes': 0,
            'failed_items': []
        }
        
        # Create a wrapper function for the thread pool
        def download_worker(asset_info):
            return self.download_single_asset(asset_info, export_path), asset_info
        
        self.log("\n" + "=" * 80, force=True)
        self.log("STARTING FILESYSTEM EXPORT", force=True)
        self.log("=" * 80, force=True)
        
        # Process downloads in parallel
        with ThreadPoolExecutor(max_workers=self.args.export_parallel_downloads) as executor:
            # Submit all download tasks
            future_to_asset = {executor.submit(download_worker, asset_info): asset_info for asset_info in download_info}
            
            for i, future in enumerate(as_completed(future_to_asset), 1):
                asset_info = future_to_asset[future]
                path = asset_info['path']
                
                try:
                    (success, message, target_path), _ = future.result()
                    
                    if success:
                        if message == "Already exists":
                            export_stats['already_exists'] += 1
                            self.debug_log(f"✓ SKIPPED: {path} (already exists)")
                        else:
                            export_stats['success'] += 1
                            export_stats['total_bytes'] += asset_info.get('size', 0)
                            self.log(f"✓ EXPORTED: {path}")
                    else:
                        export_stats['failed'] += 1
                        export_stats['failed_items'].append((path, message))
                        self.log(f"✗ FAILED: {path} ({message})")
                        
                except Exception as e:
                    export_stats['failed'] += 1
                    export_stats['failed_items'].append((path, f"Exception: {str(e)}"))
                    self.log(f"✗ ERROR: {path} ({str(e)})")
                
                # Progress update
                if i % 50 == 0:
                    elapsed = datetime.now() - self.start_time
                    rate = i / elapsed.total_seconds() if elapsed.total_seconds() > 0 else 0
                    self.log(f"  Progress: {i}/{export_stats['total']} ({i/export_stats['total']*100:.1f}%) - {rate:.2f} files/sec")
        
        # Print export summary
        self.print_export_summary(export_stats)
        return export_stats
    
    def print_export_summary(self, export_stats):
        """Print export operation summary"""
        elapsed = datetime.now() - self.start_time
        rate = export_stats['total'] / elapsed.total_seconds() if elapsed.total_seconds() > 0 else 0
        total_mb = export_stats['total_bytes'] / (1024 * 1024)
        
        self.log("\n" + "=" * 80, force=True)
        self.log("FILESYSTEM EXPORT COMPLETED", force=True) 
        self.log("=" * 80, force=True)
        self.log(f"Export location: {self.args.export_to_path}", force=True)
        self.log(f"Total files processed: {export_stats['total']}", force=True)
        self.log(f"Successfully exported: {export_stats['success']}", force=True)
        self.log(f"Already existed: {export_stats['already_exists']}", force=True)
        self.log(f"Failed: {export_stats['failed']}", force=True)
        self.log(f"Total data exported: {total_mb:.2f} MB", force=True)
        
        if export_stats['failed_items']:
            self.log("--- FAILED EXPORTS ---", force=True)
            for i, (path, reason) in enumerate(export_stats['failed_items']):
                if i < 20:  # Show first 20 failures
                    self.log(f"  - {path} (Reason: {reason})", force=True)
            if len(export_stats['failed_items']) > 20:
                self.log(f"  ... and {len(export_stats['failed_items']) - 20} more. See log file for full list.", force=True)
            self.log("------------------------", force=True)

        success_rate = (export_stats['success'] + export_stats['already_exists']) / export_stats['total'] * 100 if export_stats['total'] > 0 else 0
        self.log(f"Success rate: {success_rate:.1f}%", force=True)
        self.log(f"Total time: {str(elapsed).split('.')[0]}", force=True)
        self.log(f"Average rate: {rate:.2f} files/second", force=True)
        self.log(f"Log file: {self.log_file}", force=True)
        self.log("=" * 80, force=True)
    
    def validate_sync_completeness(self, asset_paths):
        """Validate that all assets from Nexus are available in Reposilite"""
        self.log("Validating sync completeness...", force=True)
        
        missing_files = []
        validation_errors = []
        
        for i, path in enumerate(asset_paths, 1):
            if i % 100 == 0:  # Progress update every 100 files
                self.log(f"Validation progress: {i}/{len(asset_paths)} ({i/len(asset_paths)*100:.1f}%)")
                
            url = f"{self.args.reposilite_url}/{self.args.reposilite_repository}/{path}"
            
            try:
                response = self.reposilite_session.head(url, timeout=10)
                if response.status_code == 404:
                    missing_files.append(path)
                elif response.status_code not in [200, 304]:
                    validation_errors.append((path, f"HTTP {response.status_code}"))
                    
            except Exception as e:
                validation_errors.append((path, str(e)))
                
            time.sleep(0.05)  # Small delay to avoid overwhelming the server
        
        self.log(f"Validation complete:", force=True)
        self.log(f"  Total files checked: {len(asset_paths)}", force=True)
        self.log(f"  Missing files: {len(missing_files)}", force=True)
        self.log(f"  Validation errors: {len(validation_errors)}", force=True)
        
        if missing_files:
            self.log("MISSING FILES (first 20):", force=True)
            for path in missing_files[:20]:
                self.log(f"  - {path}", force=True)
            if len(missing_files) > 20:
                self.log(f"  ... and {len(missing_files) - 20} more missing files", force=True)
                
        return len(missing_files) == 0, missing_files, validation_errors
    
    def _process_asset_paths(self, asset_paths):
        """
        Iterate through a list of asset paths, request each from Reposilite,
        and track statistics.
        """
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
        
        # Step 1: Get all asset paths from Nexus using appropriate API method(s)
        if self.args.use_assets_api_only:
            self.log("Using Assets API only (as requested)...")
            asset_paths = self.get_all_asset_paths_from_nexus()
            self.log(f"Assets API found: {len(asset_paths)} asset paths")
        elif self.args.use_components_api_only:
            self.log("Using Components API only (as requested)...")
            asset_paths = self.get_all_asset_paths_from_components_api()
            self.log(f"Components API found: {len(asset_paths)} asset paths")
        else:
            # Default: Use both APIs for maximum completeness
            self.log("Fetching assets using multiple API methods to ensure completeness...")
            
            # Method 1: Assets API
            asset_paths_from_assets = self.get_all_asset_paths_from_nexus()
            self.log(f"Assets API found: {len(asset_paths_from_assets)} asset paths")
            
            # Method 2: Components API
            asset_paths_from_components = self.get_all_asset_paths_from_components_api()
            self.log(f"Components API found: {len(asset_paths_from_components)} asset paths")
            
            # Combine and deduplicate
            asset_paths = list(set(asset_paths_from_assets + asset_paths_from_components))
            self.log(f"Combined unique asset paths: {len(asset_paths)}")
            
            # Report on differences
            assets_only = set(asset_paths_from_assets) - set(asset_paths_from_components)
            components_only = set(asset_paths_from_components) - set(asset_paths_from_assets)
            
            if assets_only:
                self.log(f"Assets API found {len(assets_only)} additional paths not in Components API")
                self.debug_log(f"Sample assets-only paths: {list(assets_only)[:5]}")
            
            if components_only:
                self.log(f"Components API found {len(components_only)} additional paths not in Assets API")
                self.debug_log(f"Sample components-only paths: {list(components_only)[:5]}")
        
        if not asset_paths:
            self.log("ERROR: No asset paths found or failed to fetch from Nexus", force=True)
            self.log("TROUBLESHOOTING:", force=True)
            self.log("1. Check if the repository name is correct with --list-repositories", force=True)
            self.log("2. Verify read permissions for the user on the repository.", force=True)
            self.log("3. The repository might actually be empty.", force=True)
            return False
        
        # Step 2: Process each artifact path (unless validation-only mode)
        if not self.args.validate_only:
            self._process_asset_paths(asset_paths)
        else:
            self.log("Skipping sync - validation-only mode requested", force=True)
        
        # Step 3: Validate sync completeness if requested
        if self.args.validate_after_sync or self.args.validate_only:
            self.log("\n" + "=" * 80, force=True)
            self.log("STARTING VALIDATION", force=True)
            self.log("=" * 80, force=True)
            
            is_complete, missing_files, validation_errors = self.validate_sync_completeness(asset_paths)
            
            if is_complete:
                self.log("✓ VALIDATION PASSED: All assets are available in Reposilite!", force=True)
            else:
                self.log(f"✗ VALIDATION FAILED: {len(missing_files)} assets are missing in Reposilite", force=True)
                
                # Save missing files to log for later analysis
                with open(self.log_file + ".missing", 'w', encoding='utf-8') as f:
                    f.write("# Missing files found during validation\n")
                    f.write(f"# Total missing: {len(missing_files)}\n")
                    f.write("# One file path per line\n\n")
                    for missing_file in missing_files:
                        f.write(f"{missing_file}\n")
                
                self.log(f"Missing files list saved to: {self.log_file}.missing", force=True)
        
        # Final summary
        self.print_summary()
        return True
    
    def sync_all_artifacts_enhanced(self):
        """Enhanced synchronization process that supports both Reposilite caching and filesystem export"""
        self.log("=" * 80, force=True)
        if self.args.export_to_path:
            if self.args.export_only:
                self.log("NEXUS FILESYSTEM EXPORT STARTED", force=True)
            else:
                self.log("NEXUS TO REPOSILITE SYNC + FILESYSTEM EXPORT STARTED", force=True)
        else:
            self.log("NEXUS TO REPOSILITE FULL EXPORT STARTED", force=True)
        self.log("=" * 80, force=True)
        self.log(f"Source: {self.args.nexus_url}/repository/{self.args.nexus_repository}", force=True)
        
        if not self.args.export_only:
            self.log(f"Reposilite Target: {self.args.reposilite_url}/{self.args.reposilite_repository}", force=True)
        if self.args.export_to_path:
            self.log(f"Export Target: {self.args.export_to_path}", force=True)
            
        self.log(f"Log file: {self.log_file}", force=True)
        
        # Step 0: Test connectivity first
        if not self.test_nexus_connectivity():
            self.log("ERROR: Cannot establish connection to Nexus - aborting", force=True)
            return False
        
        # Step 1: Get download info from Nexus APIs
        download_info = None
        
        if self.args.use_assets_api_only:
            self.log("Using Assets API only for enhanced data collection...")
            download_info = self.get_all_download_info_from_assets_api()
            self.log(f"Assets API found: {len(download_info)} assets with download info")
        elif self.args.use_components_api_only:
            self.log("Using Components API only for enhanced data collection...")
            download_info = self.get_all_download_info_from_components_api()
            self.log(f"Components API found: {len(download_info)} assets with download info")
        else:
            # Default: Use both APIs for maximum completeness
            self.log("Fetching assets using multiple API methods for enhanced data collection...")
            
            # Method 1: Assets API
            download_info_from_assets = self.get_all_download_info_from_assets_api()
            self.log(f"Assets API found: {len(download_info_from_assets)} assets with download info")
            
            # Method 2: Components API
            download_info_from_components = self.get_all_download_info_from_components_api()
            self.log(f"Components API found: {len(download_info_from_components)} assets with download info")
            
            # Combine and deduplicate based on path
            all_info = download_info_from_assets + download_info_from_components
            seen_paths = set()
            download_info = []
            for info in all_info:
                path = info['path']
                if path not in seen_paths:
                    download_info.append(info)
                    seen_paths.add(path)
                    
            self.log(f"Combined unique assets: {len(download_info)}")
            
            # Report on differences
            assets_paths = {info['path'] for info in download_info_from_assets}
            components_paths = {info['path'] for info in download_info_from_components}
            
            assets_only = assets_paths - components_paths
            components_only = components_paths - assets_paths
            
            if assets_only:
                self.log(f"Assets API found {len(assets_only)} additional paths not in Components API")
                self.debug_log(f"Sample assets-only paths: {list(assets_only)[:5]}")
            
            if components_only:
                self.log(f"Components API found {len(components_only)} additional paths not in Assets API")
                self.debug_log(f"Sample components-only paths: {list(components_only)[:5]}")
        
        if not download_info:
            self.log("ERROR: No assets found or failed to fetch from Nexus", force=True)
            self.log("TROUBLESHOOTING:", force=True)
            self.log("1. Check if the repository name is correct with --list-repositories", force=True)
            self.log("2. Verify read permissions for the user on the repository.", force=True)
            self.log("3. The repository might actually be empty.", force=True)
            return False
        
        # Step 2: Process filesystem export if requested
        export_stats = None
        if self.args.export_to_path:
            export_stats = self.export_artifacts_to_filesystem(download_info)
        
        # Step 3: Process Reposilite caching (unless export-only mode)
        if not self.args.export_only:
            # Convert download_info back to asset_paths for existing logic
            asset_paths = [info['path'] for info in download_info]
            
            if not self.args.validate_only:
                self._process_asset_paths(asset_paths)
            else:
                self.log("Skipping Reposilite sync - validation-only mode requested", force=True)
        
        # Step 4: Validate sync completeness if requested
        if (self.args.validate_after_sync or self.args.validate_only) and not self.args.export_only:
            self.log("\n" + "=" * 80, force=True)
            self.log("STARTING REPOSILITE VALIDATION", force=True)
            self.log("=" * 80, force=True)
            
            asset_paths = [info['path'] for info in download_info]
            is_complete, missing_files, validation_errors = self.validate_sync_completeness(asset_paths)
            
            if is_complete:
                self.log("✓ VALIDATION PASSED: All assets are available in Reposilite!", force=True)
            else:
                self.log(f"✗ VALIDATION FAILED: {len(missing_files)} assets are missing in Reposilite", force=True)
                
                # Save missing files to log for later analysis
                with open(self.log_file + ".missing", 'w', encoding='utf-8') as f:
                    f.write("# Missing files found during validation\n")
                    f.write(f"# Total missing: {len(missing_files)}\n")
                    f.write("# One file path per line\n\n")
                    for missing_file in missing_files:
                        f.write(f"{missing_file}\n")
                
                self.log(f"Missing files list saved to: {self.log_file}.missing", force=True)
        
        # Final summary
        if export_stats:
            # Export summary was already printed, just print Reposilite summary if applicable
            if not self.args.export_only:
                self.print_summary()
        else:
            self.print_summary()
        
        return True
    
    def get_all_asset_paths_for_gavs(self, gavs):
        """
        For a given list of GAVs, search Nexus for all associated asset paths.
        """
        self.log(f"Fetching all asset paths for {len(gavs)} GAVs...")
        all_asset_paths = set()
        
        for i, (group_id, artifact_id, version) in enumerate(gavs, 1):
            self.log(f"[{i}/{len(gavs)}] Searching assets for GAV: {group_id}:{artifact_id}:{version}")
            continuation_token = None
            
            while True:
                params = {
                    'repository': self.args.nexus_repository,
                    'group': group_id,
                    'name': artifact_id,
                    'version': version,
                }
                if continuation_token:
                    params['continuationToken'] = continuation_token

                url = f"{self.args.nexus_url}/service/rest/v1/search"
                
                try:
                    response = self.nexus_session.get(url, params=params, timeout=self.args.timeout)

                    if response.status_code != 200:
                        self.log(f"  ERROR: HTTP {response.status_code} fetching assets for GAV. Skipping.")
                        self.debug_log(f"  Response content: {response.text[:500]}...")
                        break

                    data = response.json()
                    for item in data.get('items', []):
                        for asset in item.get('assets', []):
                            if asset.get('path'):
                                all_asset_paths.add(asset['path'])
                    
                    continuation_token = data.get('continuationToken')
                    if not continuation_token:
                        break
                        
                except Exception as e:
                    self.log(f"  ERROR fetching assets for GAV: {e}")
                    break
            
            time.sleep(0.1) # Small delay to avoid overwhelming Nexus
            
        self.log(f"Found {len(all_asset_paths)} unique asset paths for all GAVs.")
        return list(all_asset_paths)

    def sync_by_gav(self):
        """
        Orchestrates the sync process by first discovering all GAVs,
        then fetching all their assets, and finally processing them.
        """
        self.log("=" * 80, force=True)
        self.log("NEXUS TO REPOSILITE FULL EXPORT (GAV-BASED) STARTED", force=True)
        self.log("=" * 80, force=True)
        
        # Step 0: Test connectivity
        if not self.test_nexus_connectivity():
            return False

        # Step 1: Discover all unique GAVs
        gavs = self.get_all_gavs_from_nexus()
        if not gavs:
            self.log("ERROR: No GAVs found or failed to fetch from Nexus", force=True)
            self.log("Ensure the repository is not empty and has read permissions.", force=True)
            return False

        # Step 2: Get all asset paths for all GAVs
        asset_paths = self.get_all_asset_paths_for_gavs(gavs)
        if not asset_paths:
            self.log("ERROR: No asset paths found for the discovered GAVs.", force=True)
            return False
            
        # Step 3: Process all asset paths
        self._process_asset_paths(asset_paths)
        
        # Step 4: Final summary
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

    def generate_tree_view(self, output_file=None):
        """Generates a file tree view of all artifacts in the repository."""
        self.log("Generating artifact tree view...", force=True)
        if not self.test_nexus_connectivity():
            self.log("ERROR: Cannot connect to Nexus.", force=True)
            return False

        asset_paths = self.get_all_asset_paths_from_nexus()
        if not asset_paths:
            self.log("No assets found to build the tree.", force=True)
            return False

        self.log(f"Building tree from {len(asset_paths)} asset paths...", force=True)
        tree = self._build_tree_from_paths(asset_paths)
        
        self.log("Printing tree...", force=True)
        
        output_handle = open(output_file, 'w', encoding='utf-8') if output_file else sys.stdout
        try:
            self._print_tree(tree, output_handle=output_handle)
        finally:
            if output_file and output_handle is not sys.stdout:
                output_handle.close()
        
        if output_file:
            self.log(f"Tree view written to {output_file}", force=True)
        
        return True

    def _build_tree_from_paths(self, paths):
        """Build a hierarchical dictionary from a list of file paths."""
        tree = {}
        for path in sorted(paths):
            parts = path.split('/')
            d = tree
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = None  # Mark as a file
        return tree

    def _print_tree(self, tree, prefix="", output_handle=sys.stdout):
        """Recursively prints a tree structure to the given output handle."""
        items = sorted(list(tree.keys()))
        for i, name in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            
            output_handle.write(f"{prefix}{connector}{name}\n")
            
            if isinstance(tree[name], dict):  # It's a directory
                new_prefix = prefix + ("    " if is_last else "│   ")
                self._print_tree(tree[name], prefix=new_prefix, output_handle=output_handle)

def main():
    args = parse_arguments()
    
    # Handle repository listing mode
    if args.list_repositories:
        print("Nexus Repository Discovery Tool")
        print("=" * 50)
        print(f"Nexus URL: {args.nexus_url}")
        if args.nexus_username:
            print(f"Authentication: {args.nexus_username}")
        print()
        
        # Create syncer just for repository listing
        syncer = NexusToReposiliteSyncer(args)
        success = syncer.list_nexus_repositories()
        
        if success:
            print("\nTo sync artifacts from a specific repository, use:")
            print(f"python3 {sys.argv[0]} --nexus-repository <repository-name>")
        
        sys.exit(0 if success else 1)
    
    if args.list_gavs:
        print("Nexus GAV Discovery Tool")
        print("=" * 50)
        print(f"Source: {args.nexus_url}/repository/{args.nexus_repository}")
        if args.nexus_username:
            print(f"Nexus authentication: {args.nexus_username}")
        print()

        syncer = NexusToReposiliteSyncer(args)
        if not syncer.test_nexus_connectivity():
            sys.exit(1)

        gavs = syncer.get_all_gavs_from_nexus()
        print(f"Total unique GAVs: {len(gavs)}")

        # Output handling
        if args.gavs_output:
            output_path = args.gavs_output
            try:
                if output_path.lower().endswith('.json'):
                    # Write JSON array of objects
                    gav_list = [
                        {"groupId": g, "artifactId": a, "version": v}
                        for (g, a, v) in sorted(gavs)
                    ]
                    with open(output_path, 'w', encoding='utf-8') as f:
                        json.dump(gav_list, f, indent=2)
                else:
                    # Write one GAV per line
                    with open(output_path, 'w', encoding='utf-8') as f:
                        for g, a, v in sorted(gavs):
                            f.write(f"{g}:{a}:{v}\n")
                print(f"Wrote GAVs to: {output_path}")
                sys.exit(0)
            except Exception as e:
                print(f"ERROR: Failed to write GAVs to file: {e}")
                sys.exit(1)
        else:
            # Print all to stdout
            for g, a, v in sorted(gavs):
                print(f"{g}:{a}:{v}")
            sys.exit(0)
    
    if args.generate_tree_view:
        print("Nexus Artifact Tree View Generator")
        print("=" * 50)
        print(f"Source: {args.nexus_url}/repository/{args.nexus_repository}")
        if args.nexus_username:
            print(f"Nexus authentication: {args.nexus_username}")
        print()

        syncer = NexusToReposiliteSyncer(args)
        success = syncer.generate_tree_view(output_file=args.tree_output_file)
        sys.exit(0 if success else 1)
    
    if args.validate_only:
        print("Nexus to Reposilite Validation Tool")
        print("=" * 50)
        print(f"Source: {args.nexus_url}/repository/{args.nexus_repository}")
        print(f"Target: {args.reposilite_url}/{args.reposilite_repository}")
        if args.nexus_username:
            print(f"Nexus authentication: {args.nexus_username}")
        print("\n🔍 VALIDATION MODE: Will check completeness without syncing")
        print()
    elif args.export_to_path:
        if args.export_only:
            print("Nexus Filesystem Export Tool")
        else:
            print("Nexus to Reposilite + Filesystem Export Tool")
        print("=" * 50)
    else:
        print("Nexus to Reposilite Full Export Tool")
        print("=" * 50)
    
    # Display configuration
    print(f"Source: {args.nexus_url}/repository/{args.nexus_repository}")
    if not args.export_only:
        print(f"Reposilite Target: {args.reposilite_url}/{args.reposilite_repository}")
    if args.export_to_path:
        print(f"Export Target: {args.export_to_path}")
        print(f"Directory Structure: {'Preserved' if args.preserve_structure else 'Flat'}")
        print(f"Parallel Downloads: {args.export_parallel_downloads}")
    if not args.export_only:
        print(f"Rate limit: {args.rate_limit} requests/second")
    print(f"Timeout: {args.timeout} seconds")
    if args.nexus_username:
        print(f"Nexus authentication: {args.nexus_username}")
    print()
    
    # Show helpful hints
    print("💡 TIP: Use --list-repositories to see available repositories first")
    if args.export_to_path:
        print("📁 EXPORT: Files will be downloaded directly from Nexus using downloadUrls")
        if args.export_only:
            print("⚡ EXPORT-ONLY: Skipping Reposilite caching entirely")
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
    syncer = NexusToReposiliteSyncer(args)
    try:
        if args.sync_by_gav:
            success = syncer.sync_by_gav()
        elif args.export_to_path:
            # Use enhanced method for export functionality
            success = syncer.sync_all_artifacts_enhanced()
        else:
            # Use original method for backward compatibility
            success = syncer.sync_all_artifacts()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        syncer.log("\nERROR: Operation interrupted by user", force=True)
        if hasattr(syncer, 'print_summary'):
            syncer.print_summary()
        sys.exit(1)
    except Exception as e:
        syncer.log(f"ERROR: Unexpected error: {e}", force=True)
        if hasattr(syncer, 'print_summary'):
            syncer.print_summary()
        sys.exit(1)

if __name__ == "__main__":
    main()