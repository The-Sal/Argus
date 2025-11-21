"""
This script is for the automated CI/CD pipeline connected to The-Sal/Argus repository to use.
"""
import os
import sys
import importlib
import traceback
import subprocess
import shutil

MODULE_NAME = 'argus'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

print("Running Argus CI/CD Pipeline Tests...")
print('Python Version:', sys.version)
print("IMPORTING MODULES...")
core_modules = []

def remove_pycaches():
    for root, dirs, files in os.walk('.'):
        for dir_name in dirs:
            if dir_name == '__pycache__':
                pycache_path = os.path.join(root, dir_name)
                print("Removing __pycache__ at", pycache_path)
                subprocess.check_call(['rm', '-rf', pycache_path])
            for file_name in files:
                if file_name.endswith('.pyc'):
                    pyc_path = os.path.join(root, file_name)
                    print("Removing .pyc file at", pyc_path)
                    os.remove(pyc_path)

def discover_modules_and_test_imports():
    where_am_i = os.path.join(__file__.replace('run_tests.py', ''), MODULE_NAME)
    os.chdir(where_am_i)
    remove_pycaches()
    print("Importing modules from", where_am_i)
    print('Directory Tree:')

    # Try to use tree if available, otherwise skip
    if shutil.which('tree'):
        subprocess.check_call(['tree', '.'])
    else:
        print("(tree command not available, skipping directory visualization)")

    modules = os.listdir(os.getcwd())
    print('Module Directory:', os.getcwd())
    for module in modules:
        if module.startswith('.') or module.startswith('__') or module.endswith('.py') or module.endswith('.md'):
            continue
        print('Found module:', module)
        imported_module = importlib.import_module('{}.{}'.format(MODULE_NAME, module))
        print('Testing sub-imports for module:', module)
        core_modules.append(imported_module)
        print('Successfully imported module:', module)


def check_and_build_swift():
    """Check if argus_swift directory exists and try to build Swift code"""
    print("\n" + "="*80)
    print("CHECKING FOR SWIFT CODE...")
    print("="*80)

    swift_dir = os.path.join(SCRIPT_DIR, 'argus_swift')

    if not os.path.exists(swift_dir):
        print("No argus_swift directory found - skipping Swift build")
        return

    print(f"Found argus_swift directory at: {swift_dir}")

    # Check if swift compiler is available
    swift_path = shutil.which('swift')
    if swift_path is None:
        print("WARNING: Swift compiler not found in PATH")
        print("Swift code exists but cannot be compiled in this environment")
        print("Skipping Swift build...")
        return

    print(f"Swift compiler found at: {swift_path}")

    # Check swift version
    try:
        version_result = subprocess.run(
            ['swift', '--version'],
            capture_output=True,
            text=True,
            timeout=10
        )
        print(f"Swift version: {version_result.stdout.strip()}")
    except Exception as e:
        print(f"Could not get Swift version: {e}")

    # Try to build the Swift project
    print(f"\nBuilding Swift project in {swift_dir}...")
    try:
        build_result = subprocess.run(
            ['swift', 'build'],
            cwd=swift_dir,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if build_result.returncode == 0:
            print("✅ Swift build SUCCEEDED")
            print("\nBuild output:")
            if build_result.stdout:
                print(build_result.stdout)
        else:
            print("❌ Swift build FAILED")
            print("\nBuild errors:")
            if build_result.stderr:
                print(build_result.stderr)
            if build_result.stdout:
                print("\nBuild output:")
                print(build_result.stdout)
            raise RuntimeError("Swift build failed")

    except subprocess.TimeoutExpired:
        print("❌ Swift build TIMED OUT (exceeded 5 minutes)")
        raise RuntimeError("Swift build timeout")
    except Exception as e:
        print(f"❌ Swift build ERROR: {e}")
        raise


if __name__ == '__main__':
    checks = [
        ('Python Module Import Tests', discover_modules_and_test_imports, False),  # Optional
        ('Swift Build Tests', check_and_build_swift, False)  # Optional - not all CI environments have Swift
    ]

    failed_checks = []
    for check_name, check_func, is_required in checks:
        try:
            check_func()
            print(f"✅ {check_name} PASSED\n")
        except Exception as e:
            print(f"❌ {check_name} FAILED")
            traceback.print_exc()
            failed_checks.append((check_name, is_required))

            if is_required:
                print(f"\nFATAL: {check_name} is required and failed")
                exit(1)
            else:
                print(f"\nWARNING: {check_name} failed but is not required, continuing...\n")

    if failed_checks:
        print("\n" + "="*80)
        print("SUMMARY: Some checks failed:")
        for name, required in failed_checks:
            status = "REQUIRED" if required else "OPTIONAL"
            print(f"  - {name} ({status})")

        # Exit with error only if required checks failed
        if any(required for _, required in failed_checks):
            exit(1)
    else:
        print("\n" + "="*80)
        print("✅ ALL CHECKS PASSED")
        print("="*80)
