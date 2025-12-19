# Argus Server Installation Guide

## What You Need

### Required Tools
1. **Python 3.7+** - The installer script is written in Python
2. **SDist CLI** - Used for decrypting the downloaded files
3. **Decryption Keys** - All files are encrypted and require proper keys to decrypt

### Optional
1. **tqdm** - For better download progress bars
   ```bash
   pip install tqdm
   ```

## Installation

### Step 1: Download the Installer Script

```bash
curl -O https://raspberrypi.tail34e8af.ts.net/Misc/argus_downloader.py
chmod +x argus_downloader.py
```

### Step 2: Run the Installer

```bash
python3 argus_downloader.py -i
```

### Step 3: Follow the Prompts

The installer will:
1. Show available versions (latest is recommended)
2. Download the encrypted file 
3. Prompt for decryption password
4. Extract the archive
5. Show available builds for your system
6. Let you choose installation location

## Important Notes

- **All files are encrypted** - You must have the proper decryption keys for the installer to work
- **SDist CLI is required** - The installer uses `sdist -c -p NONE -f decrypt -a <file> <output>` to decrypt files
- **Interactive mode** - Use `-i` flag for guided installation
- **Build selection** - The installer automatically detects your system and recommends the closest matching build

## Example Installation

```bash
# Download and run
curl -O https://raspberrypi.tail34e8af.ts.net/Misc/argus_downloader.py
python3 argus_downloader.py -i

# Example output:
Select version (number): 5
Downloading Argus-latest.zip.enc...
enter aes-256-cbc decryption password: [enter your password]
Select build to install (1-3): 1
Select location (1-2): 2

✅ Successfully installed argus_server to /usr/local/bin/argus_server
```

## Running Argus Server

After installation:
```bash
# If installed system-wide
argus_server

# If installed locally
./argus_server
```

**Note**: Without the correct decryption keys, the installer cannot decrypt the downloaded files and will fail during the decryption step.
