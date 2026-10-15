# Beta 1 — LAN and Tailscale access

This document describes how remote clients reach the single Beta 1 pilot service
whose API container binds **only** to `127.0.0.1` (see
`deploy/beta1/compose.yaml`). Nothing here changes the production security
invariants: inference/admin authentication, file-backed secrets, and literal-IP
egress stay enabled, and Funnel (public Internet exposure) is never enabled.

## Topology

```
client ──HTTP──▶ 192.168.22.50:8088 ─┐
                                     ├─▶ 127.0.0.1:<api-port> ─▶ api container
client ──HTTPS─▶ nomnom.<tailnet>.ts.net ─┘        (Tailscale Serve)
```

Two host-level listeners front the loopback API. Both target the *same* service
and the *same* authentication model:

| Client | URL | Front-end | Transport |
|---|---|---|---|
| LAN (192.168.22.0/24) | `http://192.168.22.50:8088/v1` | `socat` under a `systemd --user` unit | plain HTTP |
| Tailnet | `https://nomnom.<tailnet>.ts.net/v1` | Tailscale Serve | HTTPS (Tailscale cert) |

The desired port in the original request was `8080`; that port is already
occupied on nomnom by the unrelated `matrix-comms-element` service, which was
preserved. Beta 1 therefore uses **8088** for LAN. (See
`docs/operations/beta1-runbook.md` for the port-conflict note.)

## Stable upstream target

The API container publishes an ephemeral loopback port by default. For a
persistent host listener to survive container restart/recreation, pin the port
in `deploy/beta1/.env`:

```
AETHERGATE_API_HOST_PORT=39549
```

Leave it unset to keep the default ephemeral port (discover it with
`docker compose ... port api 8000`). The listener tooling reads this value.

## Bring-up

Requires `socat` and a `systemd --user` session with lingering enabled
(`loginctl enable-linger $USER`; already enabled on nomnom).

```bash
# From the repository root:
scripts/ops/beta1-access install
scripts/ops/beta1-access status
```

`install` performs:

1. writes `~/.config/systemd/user/aethergate-beta1-lan.service` running
   `socat TCP-LISTEN:8088,bind=192.168.22.50,fork,reuseaddr TCP:127.0.0.1:<api-port>`
   and enables it (`systemctl --user enable --now`);
2. runs `tailscale serve --bg --https=443 http://127.0.0.1:<api-port>` to serve
   the loopback API on the node's MagicDNS name with an automatic Tailscale
   certificate.

Override defaults with `--lan-port`, `--lan-ip`, `--api-port`.

### Tailscale Serve is a tailnet setting

Serve must be enabled once for the tailnet in the Tailscale admin console
(`https://login.tailscale.com/f/serve`). Until it is, `install` prints the link
and the LAN listener still works. Enabling Serve does **not** enable Funnel;
nothing is exposed to the public Internet.

### Teardown

```bash
scripts/ops/beta1-access remove
```

## Firewall / network restriction

The LAN listener binds only the LAN IPv4 (`192.168.22.50`), so it is not
reachable on loopback, the Tailscale interface, or any other address. Restrict
`192.168.22.0/24 → 192.168.22.50:8088` with the host firewall if the LAN is not
fully trusted (the required `ufw`/`iptables` rule needs root and is an operator
step).

## Security: LAN plaintext HTTP bearer-token exposure

The LAN endpoint is **plain HTTP** and authorized only because the pilot runs on
an internal, switched network. Its consequences:

- Inference calls carry `Authorization: Bearer <inference key>` and admin calls
  carry `Authorization: Bearer <admin key>` in cleartext. Anyone able to
  passively observe LAN traffic on a shared segment could capture a key.
- The Tailscale endpoint is HTTPS (WireGuard-encrypted, Tailscale-issued cert),
  so tailnet clients are unaffected.

### Migration path to HTTPS on the LAN

Pick one; none changes the application:

1. **Serve the LAN with Tailscale too.** Add a second Serve mapping or bind the
   listener to the tailscale interface and use the Tailscale cert — best if all
   LAN clients can join the tailnet.
2. **Terminate TLS at a host reverse proxy.** Put `caddy`/`nginx` on
   `192.168.22.50:443` (or `8443`) in front of `127.0.0.1:<api-port>` with an
   internal-CA or LAN-certificate, and point the `socat`/listener at it instead.
3. **Expose over HTTP on loopback only and require an SSH tunnel**, as the
   original deployment note suggested, e.g.
   `ssh -N -L 8088:127.0.0.1:<api-port> draeician@nomnom`.

Whatever the choice, keep TLS termination off the container: the container port
stays on `127.0.0.1`.

## Verification

```bash
# LAN (from another host on 192.168.22.0/24):
curl -s http://192.168.22.50:8088/health/ready
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.22.50:8088/v1/models   # 401 without a key

# Tailnet (from another tailnet device):
curl -s https://nomnom.<tailnet>.ts.net/health/ready
curl -s -H "Authorization: Bearer <inference key>" https://nomnom.<tailnet>.ts.net/v1/models

# Ping identity:
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://192.168.22.50:8088/v1/chat/completions \
  -H 'Authorization: Bearer <inference key>' -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-2b","messages":[{"role":"user","content":"OK"}]}'
```
