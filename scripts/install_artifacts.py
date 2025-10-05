# Script to install system artifacts

import os
import shutil
import subprocess
import sys
import datetime


GUARDIAN_DIR = "/usr/local/guardian"


def ensure_tools():
    """
    Checks for and installs required command-line tools like 'uv'.
    This function assumes it's running with sudo privileges.
    """
    log("Checking for required tools...")

    # Check for 'uv'
    uv_path = shutil.which("uv")
    if uv_path and os.path.exists(uv_path):
        log(f"'uv' is already installed at {uv_path}.")
        return

    log("'uv' not found. Attempting to install it system-wide using dnf.")

    # Install 'uv' using dnf
    try:
        subprocess.run(
            ["dnf", "install", "-y", "uv"],
            check=True,
            capture_output=True,
            text=True
        )
        log("Successfully installed 'uv' via dnf.")

        # Verify uv is now in PATH
        new_uv_path = shutil.which("uv")
        if new_uv_path:
            log(f"'uv' is now available at {new_uv_path}")
        else:
            log("Warning: 'uv' still not found in PATH after installation. Installation might fail.")

    except subprocess.CalledProcessError as e:
        log(f"Failed to install 'uv' using dnf: {e.stderr if hasattr(e, 'stderr') else str(e)}")
        log("Trying alternative installation methods...")

        # Fallback to pip if dnf fails
        try:
            subprocess.run(
                ["pip", "install", "--user", "uv"],
                check=True,
                capture_output=True,
                text=True
            )
            log("Successfully installed 'uv' via pip.")

            # Make sure it's in PATH by creating a symlink if needed
            uv_path = shutil.which("uv")
            if not uv_path:
                # Look for it in common pip user installation location
                user_bin_path = os.path.expanduser("~/.local/bin/uv")
                if os.path.exists(user_bin_path):
                    symlink_path = "/usr/local/bin/uv"
                    log(f"Creating symlink from {user_bin_path} to {symlink_path} for system-wide access.")
                    try:
                        if os.path.exists(symlink_path):
                            os.unlink(symlink_path)
                        os.symlink(user_bin_path, symlink_path)
                    except OSError as e:
                        log(f"Warning: Could not create symlink: {e}")
        except subprocess.CalledProcessError as e:
            log(f"Failed to install 'uv': {e.stderr if hasattr(e, 'stderr') else str(e)}")
            log("Please install 'uv' manually and try again.")
            sys.exit(1)


def log(msg):
    """
    Simple logging helper. Prints timestamped messages.
    """
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def install_shared_python():
    """
    Install a shared Python environment to /usr/local/guardian/python
    """
    target_dir = os.path.join(GUARDIAN_DIR, "python")

    subprocess.run(["uv", "python", "install", "--no-bin", "--install-dir", target_dir, "--managed-python"], check=True, cwd=os.path.join(os.path.dirname(__file__), "../guardian_daemon/"))


def find_python_executable():
    """
    Find the Python executable in the shared environment.
    """
    python_dir = os.path.join(GUARDIAN_DIR, "python")

    for root, dirs, files in os.walk(python_dir):
        if "bin" in dirs:
            bin_path = os.path.join(root, "bin")
            python_path = os.path.join(bin_path, "python")
            if os.path.exists(python_path):
                return python_path
    return None


def create_guardian_user():
    """
    Create the guardian user if it does not exist.
    """

    log("Checking for guardian user...")

    try:
        subprocess.run(["id", "guardian"], check=True)
        log("Guardian user already exists.")
    except subprocess.CalledProcessError:
        log("Creating guardian user...")
        subprocess.run(
            ["useradd", "-m", "-r", "-s", "/usr/sbin/nologin", "guardian"], check=True
        )
        subprocess.run(["usermod", "-aG", "users", "guardian"], check=True)


def install_daemon():
    """
    Install the guardian daemon folder to /usr/local/guardian/guardian_daemon
    """
    source_dir = os.path.join(os.path.dirname(__file__), "../guardian_daemon/")
    target_dir = os.path.join(GUARDIAN_DIR, "guardian_daemon")
    venv_dir = os.path.join(target_dir, ".venv")
    pycache_dir = os.path.join(target_dir, "guardian_daemon/__pycache__")
    python_dir = find_python_executable()

    log("Installing guardian daemon...")

    if not os.path.exists(source_dir):
        log(f"Source directory {source_dir} does not exist.")
        sys.exit(1)

    try:
        # Remove target .venv and guardian_daemon/__pycache__ if they exist to avoid FileExistsError
        if os.path.exists(venv_dir):
            shutil.rmtree(venv_dir)
        if os.path.exists(pycache_dir):
            shutil.rmtree(pycache_dir)
        # Copy daemon files if already exists remove first
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)
        shutil.copytree(
            source_dir,
            target_dir,
            ignore=shutil.ignore_patterns(
                '.venv', '__pycache__', 'guardian_daemon/__pycache__', '.pytest_cache', 'guardian_daemon/.mypy_cache', '_site'
            )
        )
        # Remove .venv, _pycache__ and .pytest_cache if they exist
        for dir_to_remove in [".venv", "__pycache__", "guardian_daemon/__pycache__", ".pytest_cache", "guardian_daemon/.mypy_cache", "_site"]:
            dir_path = os.path.join(target_dir, dir_to_remove)
            if os.path.exists(dir_path):
                shutil.rmtree(dir_path)
        # Set ownership to root
        shutil.chown(target_dir, user="root", group="root")
        # Set permissions
        for root, dirs, files in os.walk(target_dir):
            for name in dirs:
                os.chmod(os.path.join(root, name), 0o750)
            for name in files:
                os.chmod(os.path.join(root, name), 0o640)
        # Ensure venv exists and sync dependencies
        subprocess.run(["uv", "venv", "--python", python_dir, "--directory", target_dir, venv_dir], check=True)
        subprocess.run(["uv", "sync", "--frozen", "--python", python_dir, "--directory", target_dir], check=True, cwd=target_dir)
        log(f"Installed guardian-daemon to {target_dir}")
    except PermissionError:
        log(f"Permission denied while copying to {target_dir}. Try running as root.")
        sys.exit(1)
    except Exception as e:
        log(f"Failed to install guardian-daemon: {e}")
        sys.exit(1)


def install_agent():
    """
    Install the guardian agent folder to /usr/local/guardian/guardian_agent
    """
    source_dir = os.path.join(os.path.dirname(__file__), "../guardian_agent/")
    target_dir = os.path.join(GUARDIAN_DIR, "guardian_agent")
    venv_dir = os.path.join(target_dir, ".venv")
    python_dir = find_python_executable()

    log("Installing guardian agent...")

    if not os.path.exists(source_dir):
        log(f"Source directory {source_dir} does not exist.")
        sys.exit(1)

    try:
        # Copy agent files if already exists remove first
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)
        shutil.copytree(
            source_dir,
            target_dir,
            ignore=shutil.ignore_patterns(
                '.venv', '__pycache__', 'guardian_daemon/__pycache__', '.pytest_cache', 'guardian_daemon/.mypy_cache', '_site'
            )
        )
        # Remove .venv, _pycache__ and .pytest_cache if they exist
        for dir_to_remove in [".venv", "__pycache__", "guardian_daemon/__pycache__", ".pytest_cache", "guardian_daemon/.mypy_cache", "_site"]:
            dir_path = os.path.join(target_dir, dir_to_remove)
            if os.path.exists(dir_path):
                shutil.rmtree(dir_path)
        # Set ownership to guardian:users recursively
        for root, dirs, files in os.walk(target_dir):
            shutil.chown(root, user="guardian", group="users")
            for name in dirs:
                shutil.chown(os.path.join(root, name), user="guardian", group="users")
            for name in files:
                shutil.chown(os.path.join(root, name), user="guardian", group="users")
        # Set permissions
        for root, dirs, files in os.walk(target_dir):
            for name in dirs:
                os.chmod(os.path.join(root, name), 0o750)
            for name in files:
                os.chmod(os.path.join(root, name), 0o640)

        subprocess.run(["sudo", "-u", "guardian", "uv", "venv", "--python", python_dir, "--directory", target_dir, venv_dir], check=True)
        subprocess.run(["sudo", "-u", "guardian", "uv", "sync", "--frozen", "--directory", target_dir], check=True, cwd=target_dir)
        # Ensure .venv/bin/* can be executed by guardian user AND users group
        bin_dir = os.path.join(venv_dir, "bin")
        if os.path.exists(bin_dir):
            for filename in os.listdir(bin_dir):
                file_path = os.path.join(bin_dir, filename)
                if os.path.isfile(file_path):
                    # Set permissions to rwxr-x---
                    os.chmod(file_path, 0o750)
                    # Set ownership to guardian:users
                    shutil.chown(file_path, user="guardian", group="users")
        log(f"Installed guardian-agent to {target_dir}")
    except PermissionError:
        log(f"Permission denied while copying to {target_dir}. Try running as root.")
        sys.exit(1)
    except Exception as e:
        log(f"Failed to install guardian-agent: {e}")
        sys.exit(1)


def install_systemd_units():
    """
    Install systemd service and timer units to /etc/systemd/system/
    """

    # Copy system units folder to /usr/local/guardian/systemd_units
    source_dir = os.path.join(os.path.dirname(__file__), "../systemd_units/")
    target_dir = os.path.join(GUARDIAN_DIR, "systemd_units")

    log("Installing systemd units...")
    if not os.path.exists(source_dir):
        log(f"Source directory {source_dir} does not exist.")
        sys.exit(1)

    try:
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir)
        log(f"Installed systemd units to {target_dir}")
    except PermissionError:
        log(f"Permission denied while copying to {target_dir}. Try running as root.")
        sys.exit(1)
    except Exception as e:
        log(f"Failed to install systemd units: {e}")
        sys.exit(1)
    # Set ownership to guardian:users
    shutil.chown(target_dir, user="guardian", group="users")
    # Set permissions
    for root, dirs, files in os.walk(target_dir):
        for name in dirs:
            os.chmod(os.path.join(root, name), 0o750)
        for name in files:
            os.chmod(os.path.join(root, name), 0o640)

    # Now copy daemon .service and .timer files to /etc/systemd/system/
    source_dir = os.path.join(GUARDIAN_DIR, "systemd_units/system/")
    target_dir = "/etc/systemd/system/"

    if not os.path.exists(source_dir):
        log(f"Source directory {source_dir} does not exist.")
        sys.exit(1)

    for filename in os.listdir(source_dir):
        if filename.endswith(".service") or filename.endswith(".timer"):
            src_file = os.path.join(source_dir, filename)
            dst_file = os.path.join(target_dir, filename)
            try:
                if os.path.exists(dst_file):
                    os.remove(dst_file)
                shutil.copy(src_file, dst_file)
                log(f"Installed {filename} to {target_dir}")
            except PermissionError:
                log(
                    f"Permission denied while copying {filename}. Try running as root."
                )
                sys.exit(1)
            except Exception as e:
                log(f"Failed to install {filename}: {e}")
                sys.exit(1)

    # Reload systemd to recognize new units
    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        log("Systemd daemon reloaded.")
    except subprocess.CalledProcessError as e:
        log(f"Failed to reload systemd daemon: {e}")
        sys.exit(1)
    # Enable and start guardian-daemon service
    try:
        subprocess.run(["systemctl", "enable", "guardian_daemon.service"], check=True)
        subprocess.run(["systemctl", "restart", "guardian_daemon.service"], check=True)
        log("Enabled and started guardian_daemon.service")
    except subprocess.CalledProcessError as e:
        log(f"Failed to enable/start guardian_daemon.service: {e}")
        # print journalctl logs for the service
        subprocess.run(["journalctl", "-u", "guardian_daemon.service", "--no-pager"])
        sys.exit(1)


def setup_config_directory():
    """
    Create persistent configuration directory in /etc/guardian/daemon/ and
    copy default config if none exists
    """
    config_dir = "/etc/guardian/daemon"
    # Get default config from the installed location
    daemon_dir = "/usr/local/guardian/guardian_daemon"
    default_config_path = os.path.join(daemon_dir, "default-config.yaml")
    # For development setup, try a fallback if the installed version doesn't exist
    if not os.path.exists(default_config_path):
        default_config_path = os.path.join(os.path.dirname(__file__), "../guardian_daemon/default-config.yaml")
    target_config_path = os.path.join(config_dir, "config.yaml")

    log("Setting up persistent configuration directory...")
    try:
        # Create directory if it doesn't exist
        os.makedirs(config_dir, exist_ok=True)

        # Copy default config if target doesn't exist
        if not os.path.exists(target_config_path):
            shutil.copy(default_config_path, target_config_path)
            log(f"Copied default configuration to {target_config_path}")
        else:
            log(f"Configuration already exists at {target_config_path}")

        # Set appropriate ownership and permissions
        shutil.chown(config_dir, user="guardian", group="guardian")
        os.chmod(config_dir, 0o750)  # drwxr-x---
        shutil.chown(target_config_path, user="guardian", group="guardian")
        os.chmod(target_config_path, 0o640)  # -rw-r-----

    except PermissionError:
        log(f"Permission denied while setting up {config_dir}. Try running as root.")
        sys.exit(1)
    except Exception as e:
        log(f"Failed to set up configuration directory: {e}")
        sys.exit(1)


def install_ctl():
    """
    Install the guardian ctl script to /usr/local/bin/guardian
    """
    source_dir = os.path.join(os.path.dirname(__file__), "../guardianctl/")
    target_dir = "/usr/local/guardian/guardianctl"
    venv_dir = os.path.join(target_dir, ".venv")

    log("Installing guardian ctl...")

    if not os.path.exists(source_dir):
        log(f"Source directory {source_dir} does not exist.")
        sys.exit(1)

    try:
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir)
        # Remove .venv, _pycache__ and .pytest_cache if they exist
        for dir_to_remove in [".venv", "__pycache__", "guardianctl/__pycache__", ".pytest_cache", "guardianctl/.mypy_cache", "_site"]:
            dir_path = os.path.join(target_dir, dir_to_remove)
            if os.path.exists(dir_path):
                shutil.rmtree(dir_path)
        # Set ownership to guardian:users recursively
        for root, dirs, files in os.walk(target_dir):
            shutil.chown(root, user="guardian", group="guardian")
            for name in dirs:
                shutil.chown(os.path.join(root, name), user="guardian", group="guardian")
            for name in files:
                shutil.chown(os.path.join(root, name), user="guardian", group="guardian")
        # Set permissions
        for root, dirs, files in os.walk(target_dir):
            for name in dirs:
                os.chmod(os.path.join(root, name), 0o750)
            for name in files:
                os.chmod(os.path.join(root, name), 0o640)
        # Ensure venv exists and sync dependencies
        subprocess.run(["sudo", "-u", "guardian", "uv", "python", "--directory", target_dir, "install"], check=True, cwd=target_dir)
        subprocess.run(["sudo", "-u", "guardian", "uv", "venv", "--directory", target_dir, venv_dir], check=True)
        subprocess.run(["sudo", "-u", "guardian", "uv", "sync", "--frozen", "--directory", target_dir], check=True, cwd=target_dir)
        log(f"Installed guardian ctl to {target_dir}")
    except PermissionError:
        log(f"Permission denied while copying to {target_dir}. Try running as root.")
        sys.exit(1)
    except Exception as e:
        log(f"Failed to install guardian ctl: {e}")
        sys.exit(1)

if __name__ == "__main__":
    ensure_tools()
    create_guardian_user()
    install_shared_python()
    install_daemon()
    setup_config_directory()
    install_agent()
    install_systemd_units()
    install_ctl()
