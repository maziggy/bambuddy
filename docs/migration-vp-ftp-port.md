# Migration: Virtual Printer Port Changes

## FTP Port Change (9990 → 990)

The Virtual Printer FTP server now binds **directly to port 990** instead of port 9990.
Previously, an iptables `REDIRECT` rule was required to forward port 990 to 9990.

### Why

The iptables `REDIRECT` target rewrites the destination IP to the **primary address
of the incoming network interface**. When running multiple virtual printers on
different bind IPs (e.g. secondary interfaces or IP aliases), this caused FTP
connections to be routed to the wrong virtual printer — breaking authentication
when VPs have different access codes.

By binding directly to port 990, iptables is no longer involved and each VP's
FTP server correctly receives only its own traffic.

## New Proxy Mode Ports (6000, 322)

Proxy mode now requires two additional ports:

| Port | Protocol | Purpose |
|------|----------|---------|
| 6000 | TCP | File transfer tunnel (transparent proxy, end-to-end TLS) |
| 322 | TCP | RTSP camera streaming (transparent proxy, end-to-end TLS) |

These ports are proxied automatically — no iptables rules needed. If you have
a firewall, ensure these ports are open between the slicer and Bambuddy.

## Migration Steps

### Linux (Native / systemd)

1. **Remove old iptables rules:**
   ```bash
   sudo iptables -t nat -D PREROUTING -p tcp --dport 990 -j REDIRECT --to-port 9990
   sudo iptables -t nat -D OUTPUT -o lo -p tcp --dport 990 -j REDIRECT --to-port 9990
   ```
   Repeat each command until it says "No chain/target/match by that name".

2. **Remove persistent rules** (if saved):
   - **Debian/Ubuntu:** `sudo netfilter-persistent save`
   - **Fedora/RHEL:** `sudo service iptables save`
   - **Arch:** `sudo iptables-save > /etc/iptables/iptables.rules`

3. **Verify systemd service** has `AmbientCapabilities=CAP_NET_BIND_SERVICE`:
   ```bash
   systemctl cat bambuddy | grep AmbientCapabilities
   ```
   If missing, add it to the `[Service]` section.

4. **Restart Bambuddy.** Verify FTP binds to port 990:
   ```bash
   grep "FTPS on" logs/bambuddy.log
   # Should show: Starting virtual printer implicit FTPS on <IP>:990
   ```

### Docker (Host Network)

1. **Remove old iptables rules** on the Docker host (same as above).
2. **Update and restart** the container. No other changes needed —
   the container binds directly to port 990 via `CAP_NET_BIND_SERVICE`.

### Docker (Bridge Network)

1. **Update port mapping** in `docker-compose.yml`:
   ```yaml
   # Old:
   - "990:9990"
   # New:
   - "990:990"
   ```
2. **Recreate the container:** `docker compose up -d`

### Unraid / Synology / TrueNAS / Proxmox LXC

1. **Remove any iptables redirect rules** you added for `990 -> 9990`.
   - **Unraid:** Remove the lines from `/boot/config/go`
   - **Synology:** Remove the scheduled task that added the iptables rule
2. **Update and restart** the container.

## Verification

After migration, confirm no redirect rules remain:
```bash
sudo iptables -t nat -L PREROUTING -n | grep 9990
# Should return nothing
```

Check the FTP server is binding correctly:
```bash
grep "FTPS on" logs/bambuddy.log
# Should show port 990, not 9990
```
