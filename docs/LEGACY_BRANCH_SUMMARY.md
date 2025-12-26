# Quick Summary: Legacy Branch Status

> **For detailed analysis, see [LEGACY_BRANCH_CHANGES.md](LEGACY_BRANCH_CHANGES.md)**

## TL;DR

The `legacy/polymarket-dispatcher` branch is **36 days behind** main (100+ commits) and missing **critical bug fixes**.

### 🔴 CRITICAL Issues You're Missing:

1. **Memory Leak** - Polymarket will crash on long-running processes (Issue #20)
2. **Wrong Account Balances** - Bug #41 causes incorrect portfolio valuations  
3. **Security Hole** - Missing urllib3 update with known vulnerabilities
4. **Crashes** - SIGPIPE bug #36 causes disconnection crashes

### Statistics:
- **100+ commits behind**
- **10 critical/high bugs fixed**
- **30x performance improvement** (I/O)
- **1 security update**
- **5+ new features**

### Recommendation:
**🚨 MIGRATE TO MAIN IMMEDIATELY 🚨**

```bash
git checkout legacy/polymarket-dispatcher
git merge main
# Resolve conflicts
# Test thoroughly
```

### Minimum Fixes If You Can't Migrate:
1. Apply memory leak fix (commit `e518e24`)
2. Apply account balance fix (commit `db71b12`)  
3. Update urllib3 to 2.6.0
4. Apply SIGPIPE fix (commit `6ca68da`)

**But seriously, just merge from main.** You're missing too much.

---

**Last Updated:** December 26, 2025  
**Branch Diverged:** November 20, 2025  
**Days Behind:** ~36
