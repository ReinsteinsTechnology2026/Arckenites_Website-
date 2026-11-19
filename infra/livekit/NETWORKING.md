# LiveKit / Egress networking: staging and production approach

Recording works only if **three parties can complete a WebRTC (ICE) connection to LiveKit**:
the trainer's browser, the students' browsers, and **Egress**, which is itself a headless Chrome
that joins the room as a participant named after its egress id (`EG_...`). Browsers and Egress
reach LiveKit from different places, so a single hard-coded address can work for one and fail
for the other. This is what the first real recording run found.

## What was observed (local Docker Desktop on Windows, 2026-11-19)

| LiveKit `rtc` setting | Browsers on the host | Egress (Chrome in a container) |
|---|---|---|
| `node_ip: 127.0.0.1` | connected | **failed**: LiveKit logged `participant_connection_aborted` ~19 s after Egress joined; Egress aborted with `Start signal not received` |
| no `node_ip`, `use_external_ip: false` (LiveKit advertised its container address) | connected | connected, recorded, MP4 verified |

Why: `127.0.0.1` is only meaningful on the host. Egress runs inside a container, so a LiveKit
candidate of `127.0.0.1` points at the Egress container itself and ICE never completes.
A second, unrelated finding: Egress refuses to start unless its container has enough CPU for its
cost model (default `room_composite` cost is 4 CPUs), so a small host needs
`cpu_cost.room_composite_cpu_cost` lowered, as the staging compose did (2.0 for 720p30).

Those results come from one run of a *staging* compose in which Egress shared LiveKit's network
namespace. The repository's own `docker-compose.yml` (Egress as a separate container reaching
`ws://livekit:7880` on the compose network) has **not** been run; it avoids the loopback problem
because it sets no `node_ip`, but it must be verified the same way before it is trusted.

## Rules

1. **Never set `node_ip: 127.0.0.1` in a file Egress can see.** It breaks recording. (Guarded by
   `backend/tests/test_infra_livekit_config.py`.)
2. **Egress talks to LiveKit over the internal network**, never over the public name:
   `ws_url: ws://livekit:7880` (compose service name) or `ws://127.0.0.1:7880` only when Egress
   shares LiveKit's network namespace or uses host networking.
3. **Browsers use the public signalling URL** (`LIVEKIT_URL`, `wss://...` behind TLS in production)
   and the advertised media address.
4. **Secrets**: use an API secret of 32+ characters (LiveKit warns below that), and keep the
   webhook `api_key` equal to the public key identifier. LiveKit does not expand `${VARIABLES}`
   in its YAML.
5. Nothing in staging or production should publish media ports beyond what is deliberately opened.

## Staging (one machine, Docker Desktop)

* Keep every port bound to `127.0.0.1` (compose already does).
* Do **not** set `node_ip`; leave `use_external_ip: false` so LiveKit advertises its interface
  addresses and Egress can reach it. If host browsers cannot connect through the container address
  on a given machine, fall back to TCP-only media (`rtc.tcp_port: 7881`) or run LiveKit natively
  and give Egress a different path; do not "fix" it by publishing ports on `0.0.0.0`.
* Give the Egress container at least 3 CPUs and a memory cap you have measured, and lower
  `room_composite_cpu_cost` only as far as the host allows.
* Verify with a real two-browser recording (see below) after any network change.

## Production (to be validated on the real host; not tested here)

* Run LiveKit and Egress on a **Linux host with a public IP**, ideally LiveKit with
  `network_mode: host` (LiveKit's own recommendation for production, avoids Docker NAT for UDP).
* Set the public address explicitly: `rtc.use_external_ip: true` (or `rtc.node_ip: <public IP>`),
  open the UDP media range (`port_range_start`..`port_range_end`) and TCP 7881 in the firewall,
  and put signalling behind TLS on a real DNS name.
* Enable LiveKit's TURN (`turn:` block, TLS on 443/5349) so students on restrictive networks can
  connect; this needs a certificate and DNS and is **not configured here**.
* Egress must be able to connect to the advertised media address from where it runs. If Egress
  runs on the same host and the node address is the public IP, confirm the host allows traffic to
  its own public IP (hairpin). If not, run Egress with host networking or point it at a LiveKit
  address on the private network.
* Size Egress for the number of **simultaneous** recordings: budget at least 4 free CPU cores and
  several GB of RAM per recording, and keep `recording_min_free_gb` at a value that protects the
  staging disk.
* Store recordings on persistent storage outside the application checkout; the worker publishes
  them to OneDrive (not yet validated against the real tenant).

## How to verify after any change

1. Start the stack, the API and the worker. Join as a trainer with camera or microphone on,
   approve recording, and join as a student.
2. Expect the room indicator to turn to "Recording" within ~15 s. In the LiveKit log there must be
   **no** `participant_connection_aborted` for the `EG_...` participant; in the Egress log there
   must be no `Start signal not received`.
3. End the class; expect PUBLISHED within a minute and a playable MP4.
4. Failure drills: stop the Egress container mid-class; the recording must move to FAILED within
   about a minute while the class keeps running (the watchdog in
   `backend/app/core/recording_watchdog.py`), and the trainer must see "Retry recording".
