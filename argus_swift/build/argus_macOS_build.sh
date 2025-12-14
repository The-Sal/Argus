#!/bin/bash

set -e

# Build for Apple Silicon
swift build -c release --triple arm64-apple-macosx

# Build for Intel
swift build -c release --triple x86_64-apple-macosx

# Combine into universal binary
lipo -create -output .build/release/argus_server \
  .build/arm64-apple-macosx/release/argus_server \
  .build/x86_64-apple-macosx/release/argus_server

# Verify universal binary and display architectures
echo ""
echo "=== Universal Binary Verification ==="
echo "Architectures contained in universal binary:"
lipo -archs .build/release/argus_server
echo ""
echo "Detailed architecture information:"
lipo -detailed_info .build/release/argus_server
echo ""

# Copy the universal binary to builds directory
mkdir -p ./builds/macos
cp .build/release/argus_server ./builds/macos/

# Capture detailed system information (without hostname/username)
{
  echo "=== Build Environment Information ==="
  echo "Build Date: $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
  echo ""
  echo "=== System Information ==="
  uname -srvp
  echo ""
  echo "=== macOS Version ==="
  sw_vers
  echo ""
  echo "=== Hardware Information ==="
  sysctl -n hw.machine hw.model hw.ncpu hw.memsize | paste -d '\n' - -
  echo ""
  echo "=== cURL Version ==="
  curl --version
  echo ""
  echo "=== OpenSSL Version ==="
  openssl version
  echo ""
  echo "=== Swift Version ==="
  swift --version
  echo ""
  echo "=== Xcode Version ==="
  xcodebuild -version 2>/dev/null || echo "Xcode not found or not accessible"
  echo ""
  echo "=== SDK Information ==="
  xcrun --show-sdk-version 2>/dev/null || echo "SDK information not available"
  echo ""
  echo "=== Linker Information ==="
  ld -v 2>/dev/null || echo "Linker version not available"
} > ./builds/macos/builder.txt
