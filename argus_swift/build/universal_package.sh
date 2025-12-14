#! /bin/bash

# Assumes you are inside argus_swift
set -e
echo Generating macOS Universal Binary
sh build/argus_macOS_build.sh
echo Generating Linux Binary
sh build/argus_vm_build.sh

rm -rf argus_server_dist
mkdir argus_server_dist
mv Linux argus_server_dist
mv macOS argus_server_dist

zip -r argus_dist.zip argus_server_dist
rm -rf argus_server_dist

open argus_dist.zip --reveal
