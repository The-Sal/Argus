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

# Copy the universal binary to macOS folder
mkdir -p ./macOS
cp .build/release/argus_server ./macOS/

# Capture system information (without hostname)
uname -srm > ./macOS/builder.txt
echo "" >> ./macOS/builder.txt
curl --version >> ./macOS/builder.txt
echo "" >> ./macOS/builder.txt
swift --version >> ./macOS/builder.txt
