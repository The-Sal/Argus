# Overview
As of the Argus_Swift version 0.02, Linux is now fully supported across all the modules within Argus_Swift. There is
still only one codebase and no external packages required. For macOS this changes nothing really but there are some important notes about Linux quirks


## cURL
Compiling from source is going to require a version of cURL that supports websockets since thats what cross-platform swift uses. This may or may not be the version of cURL available on your machine. This is the version of cURL (and friends) recommended

```
curl 8.14.1 (aarch64-unknown-linux-gnu) libcurl/8.14.1 OpenSSL/3.5.4 zlib/1.3.1 brotli/1.1.0 zstd/1.5.7 libidn2/2.3.8 libpsl/0.21.2 libssh2/1.11.1 nghttp2/1.64.0 nghttp3/1.8.0 librtmp/2.3 OpenLDAP/2.6.10
Release-Date: 2025-06-04, security patched: 8.14.1-2+deb13u2
Protocols: dict file ftp ftps gopher gophers http https imap imaps ipfs ipns ldap ldaps mqtt pop3 pop3s rtmp rtsp scp sftp smb smbs smtp smtps telnet tftp ws wss
Features: alt-svc AsynchDNS brotli GSS-API HSTS HTTP2 HTTP3 HTTPS-proxy IDN IPv6 Kerberos Largefile libz NTLM PSL SPNEGO SSL threadsafe TLS-SRP UnixSockets zstd
```

Depending on your view of life this maybe a violation of argus_servers original 'no dependancy' goal–though we always considered cURL as a Linux primitive given now there's a very specific version, compiled flags, etc.. etc... Could be considered a dependancy–though still no swift packages.

## Socket Quirks
Reports of a packet corruption bug may exist within the Linux version especially during high-volume times. The issue is known and understood to be because inbound packets on the Linux varient are framed properly per read. Compared to the macOS version thats backed by Network.framework where each read returns the entire packet. In terms of what constitutes 'high volume' it would be within the ballpark of ~300 msgs/s – This was specifically found within the Binance module. It's not a silent or hidden bug the argus_server will show warnings of failed packets, however its usually only 4-10 (out of normally 800-1000). Clients are not send bad data.
