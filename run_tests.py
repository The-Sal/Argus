"""
This script is for the automated CI/CD pipeline connected to The-Sal/Argus repository to use.
"""
import os
import sys
import importlib
import traceback
import subprocess

MODULE_NAME = 'argus'
os.environ['ARGUS_CACHES_DISABLED'] = '1'

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
    print('Fully Tree:')
    subprocess.check_call([
        'tree', '.'
    ])

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
        print('Module:', imported_module)


if __name__ == '__main__':
    checks = [
        discover_modules_and_test_imports
    ]

    for check in checks:
        try:
            check()
        except Exception as e:
            print('FAILED:', check.__name__)
            traceback.print_exc()
            _ = e
            exit(1)
