# Kindle board panel

A jailbroken Kindle Paperwhite 1 that shows the board (`brain/board.py`), driven over SSH by
`brain/board_push.py` on the GPU box. The Kindle only draws and reports touches.

What lives on the device, and how to put it back after a reset:

- **USBNetwork** (NiLuJe's snapshots, the `touch_pw` build), installed through the MR package
  installer in KUAL. In `usbnet/etc/config`: `USE_WIFI="true"`, `USE_WIFI_SSHD_ONLY="true"`.
  The board's own public key goes in `usbnet/etc/authorized_keys`; on firmware 5.3 and later
  root is locked, so over WiFi a key is the only way in. Enable it at boot from KUAL.
- **`keeper.sh`**, copied to `/mnt/us/hestia/keeper.sh` and run every minute by the Kindle's own
  cron (`* * * * * /mnt/us/hestia/keeper.sh` in `/etc/crontab/root`, written under `mntroot rw`).
  It restores the WiFi SSH firewall rule and dropbear if either is gone, and logs each repair.
- **An empty directory `/mnt/us/update.bin.tmp.partial`**, which blocks Amazon's over-the-air
  update from downloading and undoing the jailbreak.
- **A DHCP reservation** on the router, so the SSH host alias keeps pointing at the Kindle. An
  unreserved address moved on a WiFi drop once, and the board froze on its last frame.

Operating notes:

- Power it from a wall charger, not a computer. Board mode stops the stock UI, and plugging into
  a computer starts a switch to USB drive mode that needs that UI and hangs on a gray screen.
- A long press of the power button (20-40 s) reboots to the normal Kindle. The board service
  takes the screen back within about a minute once SSH answers.

Using the board:

- Tap a row to select it. A second tap within a minute marks it done. The black band at the
  foot also offers **Later**, which keeps the row open but serves it after the rest for a week.
  Old rows ask "still real?" with Keep (back of the queue for two weeks), Done or Trash.
- A column that does not fit ends in a "1/5 · next >" button. Tap it for the next page, oldest
  first. After the last page it returns to the first, and the board returns to page one after
  90 seconds without a tap.
- While a row is selected, or a result line is up, the black band covers the page buttons.
  Tap anywhere else once to clear it.
