# Nexus to Reposilite Synchronization Tool

A command-line tool for synchronizing artifacts from Nexus Repository Manager to Reposilite by triggering cache requests for all artifacts in a repository.

## Overview

This tool connects to a Nexus Repository Manager instance, fetches all artifacts from a specified repository, and triggers caching requests in a Reposilite instance. Instead of downloading artifacts locally, it uses HTTP HEAD requests to efficiently trigger Reposilite's proxy caching mechanism.

## Features

- **Full Repository Sync**: Fetches ALL artifacts from Nexus repositories with pagination support.
- **Efficient Caching**: Uses HEAD requests to trigger Reposilite caching without full downloads.
- **Command-Line Interface**: Flexible argument-based configuration with sensible defaults.
- **Repository Discovery**: List all available repositories in Nexus.
- **Easy to Use**: Run with no arguments for a guided setup or use command-line flags.
- **Comprehensive Logging**: Detailed logging to both console and timestamped log files.
- **Progress Tracking**: Real-time statistics and progress reporting.
- **Rate Limiting**: Configurable request rate limiting to avoid overwhelming servers.
- **Authentication Support**: Supports Nexus authentication via username/password or environment variables.
- **Robust Error Handling**: Handles network issues and API errors gracefully.
- **Quiet Mode**: Optional reduced verbosity for automated deployments.
- **Debug Mode**: Detailed logging for troubleshooting.
- **Connectivity Testing**: Pre-sync checks to ensure Nexus is reachable.

## Installation

1.  Clone this repository:
    ```bash
    git clone https://github.com/okeren-cap/reposilite-mirror-export.git
    
    ```
2.  Install Python 3.x if you haven't already.
3.  Install dependencies:
    ```bash
    pip install requests
    ```

## Quick Start

**List available repositories first:**

```bash
python3 nexus3_exporter.py --list-repositories --nexus-url <YOUR_NEXUS_URL> -u <USER>
```

**Run the synchronization:**

```bash
# Replace with your actual configuration
python3 nexus3_exporter.py \
    --nexus-url http://your-nexus-instance.com \
    --nexus-repository your-repo-name \
    --reposilite-url http://your-reposilite-instance.com \
    --reposilite-repository your-target-repo \
    -u your-nexus-user \
    -p your-nexus-password \
    --yes
```

> **Warning**
> It is strongly recommended to use environment variables or the interactive password prompt for credentials instead of passing them as command-line arguments.

## Usage

### Basic Usage

```bash
python3 nexus3_exporter.py [options]
```

### Examples

**List available repositories:**

```bash
python3 nexus3_exporter.py --list-repositories
```

**Override just the repository:**

```bash
python3 nexus3_exporter.py --nexus-repository maven-releases
```

**Automated mode (skip confirmation, quiet output):**

```bash
python3 nexus3_exporter.py --yes --quiet
```

**Full custom configuration:**

```bash
python3 nexus3_exporter.py \
    --nexus-url https://nexus.example.com \
    --nexus-repository core-releases \
    --reposilite-url http://localhost:8080 \
    --reposilite-repository releases \
    --nexus-username admin \
    --rate-limit 10
```

_(You will be prompted for the password.)_

**Using an environment variable for the password:**

```bash
export NEXUS_PASSWORD=your-secret-password
python3 nexus3_exporter.py --nexus-username admin
```

## Command-Line Arguments

Run `python3 nexus3_exporter.py --help` for a full and up-to-date list of commands.

### Configuration

- `--nexus-url`: Your Nexus instance URL.
- `--nexus-repository`: The Nexus repository to sync from.
- `--reposilite-url`: Your Reposilite instance URL.
- `--reposilite-repository`: The target repository in Reposilite.

### Behavior

- `--rate-limit`, `-r`: Requests per second.
- `--yes`, `-y`: Skip confirmation prompt.
- `--quiet`, `-q`: Reduced verbosity.
- `--log-file`, `-l`: Custom log file path.
- `--debug`: Enable detailed debug logging.
- `--timeout`: Request timeout in seconds.

### Actions

- `--list-repositories`: List available Nexus repositories and exit.

## Authentication

The tool supports multiple secure authentication methods:

1.  **Environment Variable (Recommended)**:

    ```bash
    export NEXUS_PASSWORD=your-secret-password
    python3 nexus3_exporter.py --nexus-username your-user
    ```

2.  **Interactive Password Prompt**:
    Omit the `--nexus-password` or `-p` flag, and the script will securely prompt for it.

    ```bash
    python3 nexus3_exporter.py --nexus-username your-user
    # Prompts for password
    ```

3.  **Command-line Argument (Least Secure)**:
    ```bash
    python3 nexus3_exporter.py -u your-user -p your-password
    ```

## How It Works

1.  **Connectivity Test**: Pings the Nexus server to ensure it's reachable.
2.  **Repository Discovery** (Optional): Fetches and displays a list of all available repositories from Nexus.
3.  **Asset Discovery**: Uses the Nexus REST API to discover all artifact paths within the specified repository, handling pagination for large repositories.
4.  **Cache Triggering**: For each artifact path, it issues a `HEAD` request to the corresponding Reposilite URL. This action prompts Reposilite to fetch and cache the artifact from its remote source (Nexus) without downloading the file to the client running the script.
5.  **Monitoring**: Tracks success and failure rates, providing a detailed summary upon completion.

## Requirements

- Python 3.x
- `requests` library
- Network access to both Nexus and Reposilite instances.

## Exit Codes

- `0`: Success
- `1`: Error occurred (e.g., connection failure, failed artifacts) or user interruption.
