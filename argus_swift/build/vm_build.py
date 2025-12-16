#!/usr/bin/env python3
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from json.decoder import JSONDecodeError
from pathlib import Path

with open("build_machine.json", "r") as f:
    VM_CONFIGS = json.load(f)


for i in range(len(VM_CONFIGS)):
    config = VM_CONFIGS[i]
    user_name, addr = config["host"].split("@")
    print(f"Finding a faster way to {addr}....")
    try:
        output = subprocess.check_output(
            ["/usr/local/bin/oroute", "-sresolve", config["host"]]
        )

        try:
            oroute_info = json.loads(output)
        except JSONDecodeError:
            print("Error decoding oRoute:", output)
            raise

        if oroute_info["reachable"]:
            print(f"Found a faster route -> {oroute_info['local_address']}")
            VM_CONFIGS[i]["host"] = user_name + "@" + oroute_info["local_address"]

    except subprocess.SubprocessError as e:
        print(f"Unable to find a faster route: {e}")


def get_vm_identity(host, password):
    """Query VM to auto-detect OS name, version, and architecture"""
    try:
        env = os.environ.copy()
        if password is None:
            raise ValueError("Password is None")
        env["SSHPASS"] = password

        # Get OS info, architecture, and hostname
        result = subprocess.run(
            [
                "sshpass",
                "-e",
                "ssh",
                host,
                "grep -E '^(ID=|VERSION_ID=)' /etc/os-release 2>/dev/null || echo 'ID=unknown'; "
                "uname -m; "
                "hostname",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

        lines = result.stdout.strip().split("\n")

        # Parse output
        os_id = "unknown"
        version_id = ""
        for line in lines:
            if line.startswith("ID="):
                os_id = line.split("=")[1].strip('"')
            elif line.startswith("VERSION_ID="):
                version_id = line.split("=")[1].strip('"')

        arch = lines[-2].strip() if len(lines) >= 2 else "unknown"
        hostname = lines[-1].strip() if len(lines) >= 1 else "unknown"

        # Create descriptive name: os-version-arch or hostname-arch as fallback
        if os_id != "unknown" and version_id:
            vm_name = f"{os_id}-{version_id}-{arch}"
        elif os_id != "unknown":
            vm_name = f"{os_id}-{arch}"
        else:
            vm_name = f"{hostname}-{arch}"

        return vm_name.replace(" ", "-").lower()

    except subprocess.CalledProcessError as e:
        raise Exception(
            f"Failed to connect to VM {host}: SSH command failed (exit code {e.returncode})"
        )
    except Exception as e:
        raise Exception(
            f"Failed to get VM identity for {host}: {type(e).__name__}: {str(e)}"
        )


def build_on_vm(vm_config):
    """Build project on a single VM and retrieve artifacts"""
    host = vm_config["host"]
    password = vm_config["password"]

    try:
        # Validate configuration
        if not host:
            return {
                "name": "unknown",
                "status": "failed",
                "error": "Host is not configured",
            }
        if not password:
            return {
                "name": host,
                "status": "failed",
                "error": "Password is not set (check environment variables)",
            }

        # Auto-detect VM identity
        vm_name = get_vm_identity(host, password)
        output_dir = f"./builds/{vm_name}"

        print(f"Starting build on {vm_name} ({host})...")

        env = os.environ.copy()
        env["SSHPASS"] = password

        # Create output directory
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Rsync code to VM
        subprocess.run(
            [
                "sshpass",
                "-e",
                "rsync",
                "-a",
                ".",
                f"{host}:~/swift-linux-proj/argus_server",
                "--rsync-path=mkdir -p ~/swift-linux-proj/argus_server && rsync",
                "--info=progress2",
                "--exclude",
                ".build",
                "--exclude",
                ".env",
            ],
            env=env,
            check=True,
        )

        # Build on VM
        subprocess.run(
            [
                "sshpass",
                "-e",
                "ssh",
                host,
                "cd ~/swift-linux-proj/argus_server && "
                "~/.local/share/swiftly/bin/swift build -c release --static-swift-stdlib",
            ],
            env=env,
            check=True,
        )

        # Retrieve binary
        subprocess.run(
            [
                "sshpass",
                "-e",
                "rsync",
                "-a",
                f"{host}:~/swift-linux-proj/argus_server/.build/release/argus_server",
                f"{output_dir}/",
                "--info=progress2",
            ],
            env=env,
            check=True,
        )

        # Get comprehensive system information
        result = subprocess.run(
            [
                "sshpass",
                "-e",
                "ssh",
                host,
                "echo '=== Build Environment Information ===' && "
                "echo 'Build Date:' $(date -u +\"%Y-%m-%d %H:%M:%S UTC\") && echo && "
                "echo '=== System Information ===' && "
                "uname -srvmpio && echo && "
                "echo '=== OS Release ===' && "
                "cat /etc/os-release && echo && "
                "echo '=== Kernel Version ===' && "
                "uname -r && echo && "
                "echo '=== GLIBC Version ===' && "
                "ldd --version | head -n1 && echo && "
                "echo '=== cURL Version ===' && "
                "curl --version && echo && "
                "echo '=== OpenSSL Version ===' && "
                "openssl version && echo && "
                "echo '=== libssl Version ===' && "
                "dpkg -l | grep -E '^ii.*libssl' | head -n3 || rpm -qa | grep -E 'openssl|libssl' | head -n3 || echo 'Package info not available' && echo && "
                "echo '=== Swift Version ===' && "
                "~/.local/share/swiftly/bin/swift --version && echo && "
                "echo '=== Swiftly Version ===' && "
                "~/.local/share/swiftly/bin/swiftly --version 2>/dev/null || echo 'Swiftly version not available' && echo && "
                "echo '=== Compiler Information ===' && "
                "gcc --version 2>/dev/null | head -n1 || clang --version 2>/dev/null | head -n1 || echo 'Compiler info not available' && echo && "
                "echo '=== Linker Information ===' && "
                "ld --version 2>/dev/null | head -n1 || echo 'Linker version not available' && echo && "
                "echo '=== System Libraries ===' && "
                "echo 'libc.so:' $(readlink -f /usr/lib/libc.so.* 2>/dev/null | head -n1 || echo 'not found') && "
                "echo 'libssl.so:' $(readlink -f /usr/lib/libssl.so.* 2>/dev/null | head -n1 || echo 'not found') && "
                "echo 'libcrypto.so:' $(readlink -f /usr/lib/libcrypto.so.* 2>/dev/null | head -n1 || echo 'not found')",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

        # Save builder info
        with open(f"{output_dir}/builder.txt", "w") as f:
            f.write(result.stdout)

        # Cleanup
        subprocess.run(
            [
                "sshpass",
                "-e",
                "ssh",
                host,
                "cd ~/swift-linux-proj/argus_server && rm -rf .env",
            ],
            env=env,
            check=True,
        )

        return {
            "name": vm_name,
            "host": host,
            "status": "success",
            "output_dir": output_dir,
        }

    except subprocess.CalledProcessError as e:
        error_msg = f"Command failed (exit code {e.returncode})"
        if e.stdout:
            error_msg += f"\nOutput: {e.stdout.strip()}"
        if e.stderr:
            error_msg += f"\nError: {e.stderr.strip()}"
        return {"name": host, "status": "failed", "error": error_msg}
    except Exception as e:
        return {
            "name": host,
            "status": "failed",
            "error": f"Unexpected error: {type(e).__name__}: {str(e)}",
        }


def main():
    try:
        # Validate VM configurations
        valid_configs = []
        for i, vm in enumerate(VM_CONFIGS):
            if not vm.get("host"):
                print(f"✗ VM {i + 1}: Host is not configured")
                continue
            if not vm.get("password"):
                print(
                    f"✗ {vm['host']}: Password is not set (check VM{i + 1}_PASS environment variable)"
                )
                continue
            valid_configs.append(vm)

        if not valid_configs:
            print(
                "✗ No valid VM configurations found. Please check your VM_CONFIGS and environment variables."
            )
            return

        # Build on all VMs in parallel
        with ThreadPoolExecutor(max_workers=len(valid_configs)) as executor:
            futures = {executor.submit(build_on_vm, vm): vm for vm in valid_configs}

            results = []
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                    if result["status"] == "success":
                        print(
                            f"✓ {result['name']}: Build complete → {result['output_dir']}"
                        )
                    else:
                        print(f"✗ {result.get('name', 'unknown')}: Build failed")
                        print(f"   Error: {result.get('error', 'Unknown error')}")
                except Exception as e:
                    vm = futures[future]
                    print(
                        f"✗ {vm['host']}: Critical error - {type(e).__name__}: {str(e)}"
                    )
                    results.append(
                        {
                            "name": vm["host"],
                            "status": "failed",
                            "error": f"Critical error: {str(e)}",
                        }
                    )

        # Summary
        print("\n=== Build Summary ===")
        successful = [r for r in results if r["status"] == "success"]
        failed = [r for r in results if r["status"] == "failed"]

        if successful:
            print("Successful builds:")
            for r in successful:
                print(f"  ✓ {r['name']} ({r['host']})")

        if failed:
            print("Failed builds:")
            for r in failed:
                print(f"  ✗ {r['name']}: {r['error']}")

        print(f"\nTotal: {len(successful)} successful, {len(failed)} failed")
        
        # Exit with error code if any builds failed
        if failed:
            print("\n✗ One or more VM builds failed. Halting universal build process.")
            exit(1)

    except KeyboardInterrupt:
        print("\n✗ Build interrupted by user")
    except Exception as e:
        print(f"✗ Critical error in main: {type(e).__name__}: {str(e)}")


if __name__ == "__main__":
    main()
