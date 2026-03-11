# make it so that on error the script exits immediately
set -e
# check if the cwd is the 'tests' directory, if not, exit with an error
if [ "$(basename "$PWD")" != "tests" ]; then
  echo "Error: This script must be run from the 'tests' directory."
  exit 1
fi

python3 benchmark_p2_encoding.py
python3 test_poly_dispatcher.py
python3 test_place_multiple_orders.py
python3 test_poly_dispatcher_order_lifecycle.py
