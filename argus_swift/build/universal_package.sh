#!/bin/bash

# Assumes you are inside argus_swift
set -e

echo "=== Building for macOS ==="
sh build/argus_macOS_build.sh

echo ""
echo "=== Building for Linux VMs ==="
python3 build/vm_build.py

# Check if Python script failed
if [ $? -ne 0 ]; then
    echo "✗ VM builds failed. Halting universal build process."
    exit 1
fi

# Check if any builds failed by examining results
if [ -d "builds" ]; then
    failed_count=$(find builds -name "*.failed" | wc -l 2>/dev/null || echo "0")
    if [ "$failed_count" -gt 0 ]; then
        echo "✗ One or more VM builds failed. Halting universal build process."
        exit 1
    fi
fi

echo ""
echo "=== Preparing distribution package ==="

# Remove old distribution
rm -rf argus_server_dist

# Create new distribution structure
mkdir -p argus_server_dist

# Copy all builds (maintains subdirectory structure)
cp -r builds/* argus_server_dist/

# Display the distribution structure
echo ""
echo "Distribution contents:"
tree argus_server_dist || find argus_server_dist -type f

# Create build manifest
echo ""
echo "=== Creating build manifest ==="
{
  echo "Argus Server Build Distribution"
  echo "Generated: $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
  echo ""
  echo "Included builds:"
  for dir in argus_server_dist/*/; do
    if [ -d "$dir" ]; then
      build_name=$(basename "$dir")
      if [ -f "${dir}builder.txt" ]; then
        echo ""
        echo "--- ${build_name} ---"
        head -n 5 "${dir}builder.txt"
      fi
    fi
  done
} > argus_server_dist/BUILD_MANIFEST.txt

cat argus_server_dist/BUILD_MANIFEST.txt

echo ""
echo "=== Creating distribution archive ==="
zip -r argus_dist.zip argus_server_dist
rm -rf builds

# Get file size
dist_size=$(du -h argus_dist.zip | cut -f1)
echo "Distribution package created: argus_dist.zip (${dist_size})"

# Optional encryption
if command -v sdist &> /dev/null; then
    echo ""
    echo "=== Encrypting distribution with SDist ==="
    # SDist adds .enc extension automatically, output will be argus.zip.enc
    sdist -c -p X -f encrypt -a argus_dist.zip argus.zip
    rm -f argus_dist.zip

    if [ -f argus.zip.enc ]; then
        encrypted_size=$(du -h argus.zip.enc | cut -f1)
        echo "Encrypted package created: argus.zip.enc (${encrypted_size})"
    else
        echo "Warning: Expected argus.zip.enc file not found"
    fi
else
    echo ""
    echo "SDist not found, skipping encryption"
    echo "Unencrypted distribution available as: argus_dist.zip"
fi

echo ""
echo "=== Build complete ==="
if [ -f argus.zip.enc ]; then
    ls -lh argus.zip.enc
elif [ -f argus_dist.zip ]; then
    ls -lh argus_dist.zip
fi

# Open finder/file manager to show results
if [[ "$OSTYPE" == "darwin"* ]]; then
    open .
elif command -v xdg-open &> /dev/null; then
    xdg-open . &
fi
