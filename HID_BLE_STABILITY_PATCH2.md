# HID BLE STABILITY FIX — Part 2 (drop still happening after battery service fix)
# Author: Buddy | Directive: Bruce | Modus operandi: persist until verified
# Target: C:\Users\User\AppData\Local\hermes\profiles\luke\hid-harness\firmware\src\main.cpp
# Apply all 4 changes, rebuild, reflash, re-pair.

## DIAGNOSIS (why it still drops)
Battery service was necessary but NOT sufficient. Remaining known causes of
ESP32-NimBLE <-> Windows 10 BLE HID drops:
1. Default supervision timeout (5s) — Windows 10 occasionally delays connection
   events (especially with new BT stack updates) beyond 5s -> stack tears down.
2. Disconnect reason never logged — blind fixes are guessing.
3. No auto-reconnect — one drop = dead device until manual re-pair.
4. Connection interval too wide — Windows picks slow intervals; fixed narrow range helps.

## CHANGE 1 — Raise supervision timeout (in setup(), before bleServer start)
    NimBLEDevice::setDisconnectionMode(true);  // not the answer — keep
    // ADD:
    ble_hs_cfg.supervision_timeout = 3200;  // 3200 * 10ms = 32s (was ~5s default)

## CHANGE 2 — Log disconnect reason (inside onDisconnect / registerConnection)
    void onDisconnect(NimBLEConnInfo info) {
      Serial.printf("[BLE] DISCONNECT peer=%s reason=0x%02X (0x13=user, 0x15=supervision, 0x08=conn lost)\n",
                    info.peerAddress.toString().c_str(), info.reason);
    }
    Reason code = the real diagnosis. If 0x15 -> supervision timeout (fix 1).
    If 0x08 -> RF loss (power/antenna/distance — check CH9102 cable, move closer).
    If 0x13 -> local disconnect (our stack or Windows side).

## CHANGE 3 — Auto reconnect after drop (in onDisconnect)
    if (info.reason != 0x13) {  // not user-initiated
      Serial.println("[BLE] Re-advertising in 2s...");
      NimBLEDevice::startAdvertising();  // device reappears, Windows auto-reconnects if paired
    }

## CHANGE 4 — Tighten connection interval (in onConnect)
    hidSvc->... (existing)
    // ADD after accepting connection:
    bleServer->updateConnParams(info.connHandle, 16, 48, 0, 400);  // 16-48 * 1.25ms = 20-60ms
    // (no ble_gap_update_params force-reject pattern — soft update only)

## TEST SEQUENCE (must pass all before reporting done — modus operandi)
  1. Delete ESP32-KBM from Windows Bluetooth devices.
  2. Re-pair.
  3. Run workshop-hid status -> ble: true.
  4. Run 5x rapid workshop-hid type/key commands -> all ACK, no drop.
  5. Wait 10 minutes idle -> ble: still true (catches supervision timeout drops).
  6. Serial log clean: no [BLE] DISCONNECT events, or if present, reason logged
     and auto-reconnect recovered within 2s.
  7. Report reason code seen (if any) + pass/fail of each step.

## FALLBACK IF STILL DROPS AFTER 1-6
  - 0x08 RF loss -> check USB cable power (CH9102 underpowered?), shorten distance,
    test another USB port (non-hub).
  - 0x15 recurring -> raise supervision to 6400 and limit MSL/feature flags.
  - Windows "newer BT stack" -> disable BT stack auto power saving:
    Device Manager -> Bluetooth -> Power Management -> uncheck "Allow the computer
    to turn off this device to save power" for BOTH the BT adapter and ESP32-KBM.

Signed: Buddy — persist until step 6 verified. No stopping.
