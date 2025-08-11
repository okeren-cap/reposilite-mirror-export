#!/usr/bin/env python3
import requests
import json
import sys
import argparse
import os
from datetime import datetime
from collections import defaultdict, Counter
import re

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Nexus API Discovery Tool - Explore and visualize Nexus API responses',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s --list-repositories                    # List all repositories
  %(prog)s --explore-components                   # Explore components API
  %(prog)s --explore-assets                       # Explore assets API
  %(prog)s --compare-apis                         # Compare both APIs side by side
  %(prog)s --analyze-patterns                    # Analyze file patterns and structure
  %(prog)s --sample-size 10                      # Limit sample size for faster analysis
        '''
    )
    
    # Configuration arguments
    parser.add_argument('--nexus-url', 
                       default='http://nexus-deploy.ptsupport:8082',
                       help='Nexus base URL (default: http://nexus-deploy.ptsupport:8082)')
    parser.add_argument('--nexus-repository',
                       default='core-releases', 
                       help='Nexus repository name (default: core-releases)')
    parser.add_argument('--nexus-username', '-u',
                       help='Nexus username for authentication')
    parser.add_argument('--nexus-password', '-p',
                       help='Nexus password (or set NEXUS_PASSWORD env var)')
    parser.add_argument('--timeout', type=int, default=60,
                        help='Request timeout in seconds (default: 60)')
    
    # Discovery modes
    parser.add_argument('--list-repositories', action='store_true',
                       help='List available repositories and their details')
    parser.add_argument('--explore-components', action='store_true',
                       help='Explore the Components API and show detailed structure')
    parser.add_argument('--explore-assets', action='store_true',
                       help='Explore the Assets API and show detailed structure')
    parser.add_argument('--compare-apis', action='store_true',
                       help='Compare Components API vs Assets API side by side')
    parser.add_argument('--analyze-patterns', action='store_true',
                       help='Analyze file patterns, extensions, and directory structure')
    
    # Analysis options
    parser.add_argument('--sample-size', type=int, default=50,
                       help='Number of items to sample for analysis (default: 50)')
    parser.add_argument('--max-depth', type=int, default=5,
                       help='Maximum directory depth to analyze (default: 5)')
    parser.add_argument('--output-format', choices=['text', 'json', 'csv'], default='text',
                       help='Output format for detailed analysis (default: text)')
    parser.add_argument('--save-samples', action='store_true',
                       help='Save sample API responses to files for inspection')
    
    args = parser.parse_args()
    
    # Handle password from environment variable if not provided
    if args.nexus_username and not args.nexus_password:
        args.nexus_password = os.getenv('NEXUS_PASSWORD')
        if not args.nexus_password:
            if not sys.stdout.isatty():
                print("ERROR: Nexus username provided without a password in a non-interactive session.", file=sys.stderr)
                print("Please provide --nexus-password or set NEXUS_PASSWORD environment variable.", file=sys.stderr)
                sys.exit(1)
            import getpass
            args.nexus_password = getpass.getpass('Nexus password: ')
    
    return args

class NexusAPIDiscovery:
    def __init__(self, args):
        self.args = args
        self.session = requests.Session()
        if args.nexus_username and args.nexus_password:
            self.session.auth = (args.nexus_username, args.nexus_password)
        
        # Statistics and data storage
        self.components_data = []
        self.assets_data = []
        self.sample_responses = {}
        
    def print_header(self, title):
        """Print a formatted header"""
        print("\n" + "=" * 80)
        print(f"🔍 {title}")
        print("=" * 80)
    
    def print_section(self, title):
        """Print a formatted section header"""
        print(f"\n📋 {title}")
        print("-" * 60)
    
    def print_subsection(self, title):
        """Print a formatted subsection header"""
        print(f"\n  📌 {title}")
        print("  " + "-" * 40)
    
    def save_sample_response(self, api_name, data):
        """Save sample API response to file"""
        if self.args.save_samples:
            filename = f"nexus_{api_name}_sample_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"  💾 Sample saved to: {filename}")
    
    def test_connectivity(self):
        """Test basic connectivity to Nexus"""
        print("🔌 Testing Nexus connectivity...")
        status_url = f"{self.args.nexus_url}/service/rest/v1/status"
        
        try:
            response = self.session.get(status_url, timeout=self.args.timeout)
            if response.status_code == 200:
                print("✅ Nexus server is reachable")
                return True
            else:
                print(f"⚠️  Nexus status check returned HTTP {response.status_code}")
                return True  # Continue anyway
        except Exception as e:
            print(f"❌ Cannot connect to Nexus server: {e}")
            return False
    
    def list_repositories(self):
        """List all available repositories with detailed information"""
        self.print_header("NEXUS REPOSITORY DISCOVERY")
        
        if not self.test_connectivity():
            return False
        
        url = f"{self.args.nexus_url}/service/rest/v1/repositories"
        print(f"🌐 Fetching repositories from: {url}")
        
        try:
            response = self.session.get(url, timeout=self.args.timeout)
            if response.status_code != 200:
                print(f"❌ HTTP {response.status_code} - {response.reason}")
                return False
            
            repositories = response.json()
            print(f"📊 Found {len(repositories)} repositories")
            
            # Group repositories by format
            by_format = defaultdict(list)
            for repo in repositories:
                format_type = repo.get('format', 'Unknown')
                by_format[format_type].append(repo)
            
            # Display Maven2 repositories first (most relevant)
            if 'maven2' in by_format:
                self.print_section("Maven2 Repositories (Compatible with this tool)")
                for repo in sorted(by_format['maven2'], key=lambda x: x['name']):
                    self.display_repository_details(repo)
            
            # Display other formats
            for format_type, repos in by_format.items():
                if format_type != 'maven2':
                    self.print_section(f"{format_type.upper()} Repositories")
                    for repo in sorted(repos, key=lambda x: x['name']):
                        self.display_repository_details(repo)
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to list repositories: {e}")
            return False
    
    def display_repository_details(self, repo):
        """Display detailed information about a repository"""
        name = repo.get('name', 'Unknown')
        repo_type = repo.get('type', 'Unknown')
        format_type = repo.get('format', 'Unknown')
        url = repo.get('url', 'N/A')
        
        print(f"\n  📦 {name}")
        print(f"     Type: {repo_type}")
        print(f"     Format: {format_type}")
        print(f"     URL: {url}")
        
        # Show additional properties if available
        if 'attributes' in repo:
            attrs = repo['attributes']
            if 'maven' in attrs:
                maven = attrs['maven']
                print(f"     Maven Version Policy: {maven.get('versionPolicy', 'N/A')}")
                print(f"     Layout Policy: {maven.get('layoutPolicy', 'N/A')}")
    
    def explore_components_api(self):
        """Explore the Components API in detail"""
        self.print_header("COMPONENTS API EXPLORATION")
        
        if not self.test_connectivity():
            return False
        
        url = f"{self.args.nexus_url}/service/rest/v1/components"
        params = {'repository': self.args.nexus_repository}
        
        print(f"🌐 Components API URL: {url}")
        print(f"📦 Repository: {self.args.nexus_repository}")
        print(f"📊 Sample size: {self.args.sample_size}")
        
        try:
            response = self.session.get(url, params=params, timeout=self.args.timeout)
            if response.status_code != 200:
                print(f"❌ HTTP {response.status_code} - {response.reason}")
                return False
            
            data = response.json()
            self.save_sample_response('components', data)
            
            items = data.get('items', [])
            print(f"📊 Retrieved {len(items)} components")
            
            if not items:
                print("⚠️  No components found in this repository")
                return True
            
            # Analyze the first few components in detail
            sample_size = min(self.args.sample_size, len(items))
            sample_components = items[:sample_size]
            
            self.print_section("Sample Component Structure")
            if sample_components:
                self.analyze_component_structure(sample_components[0])
            
            self.print_section("Component Statistics")
            self.analyze_components_statistics(sample_components)
            
            self.print_section("Asset Analysis")
            self.analyze_component_assets(sample_components)
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to explore Components API: {e}")
            return False
    
    def explore_assets_api(self):
        """Explore the Assets API in detail"""
        self.print_header("ASSETS API EXPLORATION")
        
        if not self.test_connectivity():
            return False
        
        url = f"{self.args.nexus_url}/service/rest/v1/assets"
        params = {'repository': self.args.nexus_repository}
        
        print(f"🌐 Assets API URL: {url}")
        print(f"📦 Repository: {self.args.nexus_repository}")
        print(f"📊 Sample size: {self.args.sample_size}")
        
        try:
            response = self.session.get(url, params=params, timeout=self.args.timeout)
            if response.status_code != 200:
                print(f"❌ HTTP {response.status_code} - {response.reason}")
                return False
            
            data = response.json()
            self.save_sample_response('assets', data)
            
            items = data.get('items', [])
            print(f"📊 Retrieved {len(items)} assets")
            
            if not items:
                print("⚠️  No assets found in this repository")
                return True
            
            # Analyze the first few assets in detail
            sample_size = min(self.args.sample_size, len(items))
            sample_assets = items[:sample_size]
            
            self.print_section("Sample Asset Structure")
            if sample_assets:
                self.analyze_asset_structure(sample_assets[0])
            
            self.print_section("Asset Statistics")
            self.analyze_assets_statistics(sample_assets)
            
            self.print_section("File Type Analysis")
            self.analyze_file_types(sample_assets)
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to explore Assets API: {e}")
            return False
    
    def analyze_component_structure(self, component):
        """Analyze and display the structure of a component"""
        print("  📋 Component JSON Structure:")
        print(json.dumps(component, indent=4, ensure_ascii=False))
        
        self.print_subsection("Key Fields Analysis")
        
        # Extract key information
        name = component.get('name', 'N/A')
        version = component.get('version', 'N/A')
        format_type = component.get('format', 'N/A')
        group = component.get('group', 'N/A')
        assets = component.get('assets', [])
        
        print(f"    Name: {name}")
        print(f"    Version: {version}")
        print(f"    Format: {format_type}")
        print(f"    Group: {group}")
        print(f"    Assets count: {len(assets)}")
        
        if assets:
            self.print_subsection("Asset Details")
            for i, asset in enumerate(assets[:3]):  # Show first 3 assets
                path = asset.get('path', 'N/A')
                size = asset.get('fileSize', 'N/A')
                print(f"    Asset {i+1}: {path} (Size: {size})")
    
    def analyze_asset_structure(self, asset):
        """Analyze and display the structure of an asset"""
        print("  📋 Asset JSON Structure:")
        print(json.dumps(asset, indent=4, ensure_ascii=False))
        
        self.print_subsection("Key Fields Analysis")
        
        # Extract key information
        path = asset.get('path', 'N/A')
        size = asset.get('fileSize', 'N/A')
        content_type = asset.get('contentType', 'N/A')
        last_modified = asset.get('lastModified', 'N/A')
        checksum = asset.get('checksum', {})
        
        print(f"    Path: {path}")
        print(f"    Size: {size}")
        print(f"    Content-Type: {content_type}")
        print(f"    Last Modified: {last_modified}")
        print(f"    Checksum: {checksum}")
    
    def analyze_components_statistics(self, components):
        """Analyze statistics from components"""
        if not components:
            return
        
        # Count by format
        formats = Counter(comp.get('format', 'Unknown') for comp in components)
        print("  📊 Format Distribution:")
        for format_type, count in formats.most_common():
            print(f"    {format_type}: {count}")
        
        # Count by group
        groups = Counter(comp.get('group', 'Unknown') for comp in components)
        print(f"\n  📊 Top 10 Groups:")
        for group, count in groups.most_common(10):
            print(f"    {group}: {count}")
        
        # Asset count distribution
        asset_counts = [len(comp.get('assets', [])) for comp in components]
        if asset_counts:
            avg_assets = sum(asset_counts) / len(asset_counts)
            max_assets = max(asset_counts)
            min_assets = min(asset_counts)
            print(f"\n  📊 Asset Count Statistics:")
            print(f"    Average assets per component: {avg_assets:.1f}")
            print(f"    Max assets per component: {max_assets}")
            print(f"    Min assets per component: {min_assets}")
    
    def analyze_assets_statistics(self, assets):
        """Analyze statistics from assets"""
        if not assets:
            return
        
        # Size distribution
        sizes = [asset.get('fileSize', 0) for asset in assets if asset.get('fileSize')]
        if sizes:
            avg_size = sum(sizes) / len(sizes)
            max_size = max(sizes)
            min_size = min(sizes)
            print("  📊 File Size Statistics:")
            print(f"    Average size: {avg_size:,.0f} bytes ({avg_size/1024/1024:.1f} MB)")
            print(f"    Max size: {max_size:,.0f} bytes ({max_size/1024/1024:.1f} MB)")
            print(f"    Min size: {min_size:,.0f} bytes ({min_size/1024:.1f} KB)")
        
        # Content type distribution
        content_types = Counter(asset.get('contentType', 'Unknown') for asset in assets)
        print(f"\n  📊 Content Type Distribution:")
        for content_type, count in content_types.most_common(10):
            print(f"    {content_type}: {count}")
    
    def analyze_component_assets(self, components):
        """Analyze assets within components"""
        all_asset_paths = []
        for component in components:
            for asset in component.get('assets', []):
                path = asset.get('path', '')
                if path:
                    all_asset_paths.append(path)
        
        if all_asset_paths:
            self.analyze_path_patterns(all_asset_paths, "Component Assets")
    
    def analyze_file_types(self, assets):
        """Analyze file types from assets"""
        file_extensions = []
        for asset in assets:
            path = asset.get('path', '')
            if path:
                ext = os.path.splitext(path)[1].lower()
                if ext:
                    file_extensions.append(ext)
        
        if file_extensions:
            ext_counter = Counter(file_extensions)
            print("  📊 File Extension Distribution:")
            for ext, count in ext_counter.most_common(10):
                print(f"    {ext}: {count}")
    
    def compare_apis(self):
        """Compare Components API vs Assets API side by side"""
        self.print_header("COMPONENTS API vs ASSETS API COMPARISON")
        
        if not self.test_connectivity():
            return False
        
        # Fetch data from both APIs
        components_url = f"{self.args.nexus_url}/service/rest/v1/components"
        assets_url = f"{self.args.nexus_url}/service/rest/v1/assets"
        params = {'repository': self.args.nexus_repository}
        
        print("🔄 Fetching data from both APIs...")
        
        try:
            # Get Components API data
            comp_response = self.session.get(components_url, params=params, timeout=self.args.timeout)
            if comp_response.status_code != 200:
                print(f"❌ Components API: HTTP {comp_response.status_code}")
                return False
            
            comp_data = comp_response.json()
            comp_items = comp_data.get('items', [])
            
            # Get Assets API data
            assets_response = self.session.get(assets_url, params=params, timeout=self.args.timeout)
            if assets_response.status_code != 200:
                print(f"❌ Assets API: HTTP {assets_response.status_code}")
                return False
            
            assets_data = assets_response.json()
            assets_items = assets_data.get('items', [])
            
            print(f"📊 Components API: {len(comp_items)} components")
            print(f"📊 Assets API: {len(assets_items)} assets")
            
            # Extract paths from both APIs
            comp_paths = set()
            for component in comp_items:
                for asset in component.get('assets', []):
                    path = asset.get('path', '')
                    if path:
                        comp_paths.add(path)
            
            assets_paths = set()
            for asset in assets_items:
                path = asset.get('path', '')
                if path:
                    assets_paths.add(path)
            
            self.print_section("Path Comparison")
            print(f"  📦 Unique paths in Components API: {len(comp_paths)}")
            print(f"  📦 Unique paths in Assets API: {len(assets_paths)}")
            
            # Find differences
            only_in_components = comp_paths - assets_paths
            only_in_assets = assets_paths - comp_paths
            common_paths = comp_paths & assets_paths
            
            print(f"  🔄 Common paths: {len(common_paths)}")
            print(f"  ➕ Only in Components API: {len(only_in_components)}")
            print(f"  ➕ Only in Assets API: {len(only_in_assets)}")
            
            if only_in_components:
                self.print_subsection("Paths Only in Components API (first 10)")
                for path in sorted(list(only_in_components)[:10]):
                    print(f"    {path}")
            
            if only_in_assets:
                self.print_subsection("Paths Only in Assets API (first 10)")
                for path in sorted(list(only_in_assets)[:10]):
                    print(f"    {path}")
            
            # Analyze structure differences
            self.print_section("API Structure Comparison")
            
            if comp_items:
                comp_sample = comp_items[0]
                print("  📋 Components API Sample Structure:")
                print(f"    Keys: {list(comp_sample.keys())}")
                if 'assets' in comp_sample:
                    print(f"    Assets count: {len(comp_sample['assets'])}")
                    if comp_sample['assets']:
                        print(f"    Asset keys: {list(comp_sample['assets'][0].keys())}")
            
            if assets_items:
                assets_sample = assets_items[0]
                print("  📋 Assets API Sample Structure:")
                print(f"    Keys: {list(assets_sample.keys())}")
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to compare APIs: {e}")
            return False
    
    def analyze_patterns(self):
        """Analyze file patterns, extensions, and directory structure"""
        self.print_header("PATTERN ANALYSIS")
        
        if not self.test_connectivity():
            return False
        
        # Get data from both APIs for comprehensive analysis
        components_url = f"{self.args.nexus_url}/service/rest/v1/components"
        assets_url = f"{self.args.nexus_url}/service/rest/v1/assets"
        params = {'repository': self.args.nexus_repository}
        
        print("🔄 Fetching data for pattern analysis...")
        
        try:
            # Get Components API data
            comp_response = self.session.get(components_url, params=params, timeout=self.args.timeout)
            if comp_response.status_code != 200:
                print(f"❌ Components API: HTTP {comp_response.status_code}")
                return False
            
            comp_data = comp_response.json()
            comp_items = comp_data.get('items', [])
            
            # Get Assets API data
            assets_response = self.session.get(assets_url, params=params, timeout=self.args.timeout)
            if assets_response.status_code != 200:
                print(f"❌ Assets API: HTTP {assets_response.status_code}")
                return False
            
            assets_data = assets_response.json()
            assets_items = assets_data.get('items', [])
            
            # Extract all paths
            all_paths = set()
            
            # From Components API
            for component in comp_items:
                for asset in component.get('assets', []):
                    path = asset.get('path', '')
                    if path:
                        all_paths.add(path)
            
            # From Assets API
            for asset in assets_items:
                path = asset.get('path', '')
                if path:
                    all_paths.add(path)
            
            print(f"📊 Total unique paths found: {len(all_paths)}")
            
            if not all_paths:
                print("⚠️  No paths found for analysis")
                return True
            
            # Limit to sample size for analysis
            sample_paths = list(all_paths)[:self.args.sample_size]
            
            self.analyze_path_patterns(sample_paths, "All Paths")
            self.analyze_directory_structure(sample_paths)
            self.analyze_naming_patterns(sample_paths)
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to analyze patterns: {e}")
            return False
    
    def analyze_path_patterns(self, paths, title):
        """Analyze patterns in file paths"""
        self.print_section(f"{title} - Path Analysis")
        
        # File extensions
        extensions = []
        for path in paths:
            ext = os.path.splitext(path)[1].lower()
            if ext:
                extensions.append(ext)
        
        if extensions:
            ext_counter = Counter(extensions)
            print("  📊 File Extensions:")
            for ext, count in ext_counter.most_common(10):
                print(f"    {ext}: {count}")
        
        # Path depth analysis
        depths = [len(path.split('/')) for path in paths]
        if depths:
            avg_depth = sum(depths) / len(depths)
            max_depth = max(depths)
            min_depth = min(depths)
            print(f"\n  📊 Path Depth Statistics:")
            print(f"    Average depth: {avg_depth:.1f}")
            print(f"    Max depth: {max_depth}")
            print(f"    Min depth: {min_depth}")
        
        # Common prefixes
        prefixes = defaultdict(int)
        for path in paths:
            parts = path.split('/')
            for i in range(1, min(len(parts), self.args.max_depth + 1)):
                prefix = '/'.join(parts[:i])
                prefixes[prefix] += 1
        
        print(f"\n  📊 Top 10 Common Path Prefixes:")
        for prefix, count in sorted(prefixes.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"    {prefix}: {count} files")
    
    def analyze_directory_structure(self, paths):
        """Analyze directory structure patterns"""
        self.print_section("Directory Structure Analysis")
        
        # Group by top-level directories
        top_level = defaultdict(list)
        for path in paths:
            parts = path.split('/')
            if len(parts) > 1:
                top_level[parts[0]].append(path)
        
        print("  📊 Top-Level Directories:")
        for directory, dir_paths in sorted(top_level.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
            print(f"    {directory}: {len(dir_paths)} files")
            
            # Show subdirectories for top directories
            if len(dir_paths) > 1:
                subdirs = defaultdict(int)
                for path in dir_paths:
                    parts = path.split('/')
                    if len(parts) > 2:
                        subdir = f"{parts[0]}/{parts[1]}"
                        subdirs[subdir] += 1
                
                if subdirs:
                    print(f"      Top subdirectories:")
                    for subdir, count in sorted(subdirs.items(), key=lambda x: x[1], reverse=True)[:5]:
                        print(f"        {subdir}: {count} files")
    
    def analyze_naming_patterns(self, paths):
        """Analyze naming patterns in files"""
        self.print_section("Naming Pattern Analysis")
        
        # Extract filenames
        filenames = [os.path.basename(path) for path in paths if path]
        
        # Version patterns (common in Maven)
        version_patterns = []
        for filename in filenames:
            # Look for version patterns like -1.2.3, -v1.2.3, etc.
            version_match = re.search(r'-(\d+\.\d+\.\d+[-\w]*)', filename)
            if version_match:
                version_patterns.append(version_match.group(1))
        
        if version_patterns:
            version_counter = Counter(version_patterns)
            print("  📊 Common Version Patterns:")
            for version, count in version_counter.most_common(10):
                print(f"    {version}: {count} files")
        
        # File naming patterns
        naming_patterns = []
        for filename in filenames:
            # Extract pattern before extension
            name_without_ext = os.path.splitext(filename)[0]
            if name_without_ext:
                naming_patterns.append(name_without_ext)
        
        if naming_patterns:
            pattern_counter = Counter(naming_patterns)
            print(f"\n  📊 Common File Naming Patterns:")
            for pattern, count in pattern_counter.most_common(10):
                print(f"    {pattern}: {count} files")

def main():
    args = parse_arguments()
    discovery = NexusAPIDiscovery(args)
    
    print("🔍 Nexus API Discovery Tool")
    print("=" * 50)
    print(f"Nexus URL: {args.nexus_url}")
    if args.nexus_username:
        print(f"Authentication: {args.nexus_username}")
    print()
    
    success = True
    
    if args.list_repositories:
        success = discovery.list_repositories()
    
    if args.explore_components:
        success = discovery.explore_components_api() and success
    
    if args.explore_assets:
        success = discovery.explore_assets_api() and success
    
    if args.compare_apis:
        success = discovery.compare_apis() and success
    
    if args.analyze_patterns:
        success = discovery.analyze_patterns() and success
    
    # If no specific mode was selected, show help
    if not any([args.list_repositories, args.explore_components, args.explore_assets, 
                args.compare_apis, args.analyze_patterns]):
        print("💡 No discovery mode selected. Use one of these options:")
        print("  --list-repositories     - List all available repositories")
        print("  --explore-components    - Explore Components API structure")
        print("  --explore-assets        - Explore Assets API structure")
        print("  --compare-apis          - Compare both APIs side by side")
        print("  --analyze-patterns      - Analyze file patterns and structure")
        print("\nExample:")
        print(f"  python3 {sys.argv[0]} --list-repositories")
        print(f"  python3 {sys.argv[0]} --compare-apis --sample-size 20")
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main() 