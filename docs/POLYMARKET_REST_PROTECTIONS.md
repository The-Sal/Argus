# Polymarket REST API Protections

This document outlines the multi-layered protection system implemented in the Polymarket Direct REST API client to ensure safe and compliant access to Polymarket services.

## Overview

The `PolyRestAPI` class implements a three-stage protection system that safeguards both the user and Polymarket from geo-blocked access and potential compliance violations. These protections are designed to prevent orders from being placed or API credentials from being exposed from restricted jurisdictions.
All connections are WireProxy-aware with the ID 'POLYMARKET' just like the REST client.

## Protection Stages

### Stage 1: Pre-Connection IP Safety Check

**Location**: `IPSafety` class, lines 34-50

**Purpose**: This is the first line of defense that operates **before any connection to Polymarket is made**.

**How it works**:
1. Uses `ipinfo.io/json` to fetch current IP information
2. Checks the country code against a hardcoded list of known geo-blocked regions
3. **Important**: This check never exposes your IP to Polymarket - only to ipinfo.io

**Known Bad Regions**:
```python
KNOWN_BAD_REGIONS = ["US", "GB", "FR", "DE", "IT", "BE", "PL", "AU", "SG", "TW",
                     "TH", "RU", "BY", "CU", "IR", "IQ", "KP", "SY", "VE", "MM", 
                     "LY", "SD", "SS", "SO", "YE", "ZW", "LB", "ET", "NI", "BI", 
                     "CF", "CD", "UM", "AE"]
```

**Environmental Variable**: `POLYMARKET_PARANOID`
- **Default**: `false`
- **When `true`**: If IP is in bad region, immediately terminates with RuntimeError
- **When `false`**: Shows warning but proceeds to Stage 2 for verification

**Why this exists**: This provides an early warning system without exposing your IP address to Polymarket's systems, maintaining privacy while checking against known restrictions.

### Stage 2: Direct Polymarket Geo-Block Verification

**Location**: `check_geo_blocked()` method, lines 146-155

**Purpose**: This is the **first actual connection to Polymarket** to verify if their systems specifically block your IP.

**How it works**:
1. Makes a request to `https://polymarket.com/api/geoblock`
2. Polymarket's servers determine if your IP should be blocked
3. Returns a boolean indicating blocked status

**Environmental Variable**: `POLYMARKET_PROTECTION`
- **Default**: `true`
- **When `true`**: Performs this check and raises RuntimeError if blocked
- **When `false`**: **DANGEROUS** - Skips this protection entirely

**Warning when disabled**: The system provides a 30-second countdown with multiple warnings (visual and auditory) before proceeding, as this step exposes your IP to Polymarket and bypasses critical safety checks.

**Why this exists**: This provides the definitive answer from Polymarket's own systems about whether your specific IP is blocked, going beyond hardcoded region lists. If your IP 
is blocked here, YOU CANNOT TRADE IT WILL BE REJECTED FROM POLYMARKET. The only reason `POLYMARKET_PROTECTION` allows you to continue is if you are only accessing market data
whicgh _MAY_ be allowed from blocked regions. All order placement attempts will fail and raise errors.

### Stage 3: Cache Layer Protection

**Location**: `DomainCache` usage, lines 12 and 157-164

**Purpose**: Reduces unnecessary API calls and provides an additional layer of rate limiting.

**How it works**:
1. Uses the `DomainCache` system with 1-hour expiration for API credentials
2. Caches responses to minimize repeated exposure to Polymarket
3. Only hits Polymarket API when cache is expired or empty

**Cache Details**:
- **Function**: `_create_or_derive_api_creds`
- **Expiration**: 60 minutes (3600 seconds)
- **Condition**: Only caches successful responses (`should_cache_function=lambda x: x is not None`)

**Why this exists**: This minimizes the frequency of API calls, reducing both the risk of triggering rate limits and the exposure of your IP address to Polymarket's monitoring systems.

## Protection Flow Summary

```
Initialization Start
         ↓
Stage 1: IP Info Check (ipinfo.io) 
         ↓
If bad region + PARANOID=true → TERMINATE
         ↓
Show warning if bad region + PARANOID=false
         ↓
Stage 2: Polymarket Geo-Block Check
         ↓
If blocked → TERMINATE (unless PROTECTION=false)
         ↓
Stage 3: Cache Layer (minimize API calls)
         ↓
API Credentials Derivation (cached)
         ↓
Safe Trading Enabled
```

## Security Features

### IP Privacy Protection
- **Stage 1**: IP only exposed to ipinfo.io, not Polymarket
- **Stage 2**: Only proceeds to Polymarket if Stage 1 indicates potential safety
- **Stage 3**: Minimizes repeated API calls through caching

### Fail-Safe Mechanisms
- **Hard Termination**: Immediate exit when definite blocks are detected
- **Warning Systems**: Visual and audible warnings when bypassing protections
- **Graceful Countdowns**: 30-second warning period when dangerous configurations are detected

### Environmental Controls
- **Fine-grained Control**: Separate controls for paranoia level and protection bypassing
- **Default-Safe**: All protections enabled by default
- **Explicit Bypass**: Requires explicit action to disable safety measures

# Important Notes

1. **IP Redaction**: All logging automatically redacts IP addresses for privacy
2. **Proxy Integration**: All checks respect the WireProxy configuration and use the same proxy settings
3. **Persistent Warnings**: System uses multiple notification methods (terminal colors, sounds, macOS notifications) for critical warnings
4. **Compliance Focus**: These protections are designed to help maintain compliance with both Polymarket's terms and regional regulations

## Error Handling

The protection system provides detailed error messages that help diagnose:
- Which stage failed
- Why the failure occurred
- What environmental variables are in effect
- IP information (with redaction for privacy)

This ensures that users can troubleshoot connection issues while understanding the security implications of each protection layer.