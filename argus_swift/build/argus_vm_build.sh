#!/bin/bash
# Load .env file
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

set -e

export SSHPASS=$VM_PASS
sshpass -e rsync -a . $VM_HOST:~/swift-linux-proj/argus_server --info=progress2 --exclude .build --exclude .env
sshpass -e ssh $VM_HOST "cd ~/swift-linux-proj/argus_server  && ~/.local/share/swiftly/bin/swift build -c release"
sshpass -e rsync -a $VM_HOST:~/swift-linux-proj/argus_server/.build/release/argus_server ./Linux/ --info=progress2 --exclude .build --exclude .env
sshpass -e ssh $VM_HOST "uname -a && echo && curl --version && echo && ~/.local/share/swiftly/bin/swift --version" > ./Linux/builder.txt
sshpass -e ssh $VM_HOST "cd ~/swift-linux-proj/argus_server && rm -rf .env"
