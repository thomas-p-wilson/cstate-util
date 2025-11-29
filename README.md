# C-State Diagnostic Tool

I tire of walking through the same rote steps to diagnose C-state power
management issues on my Linux systems, only to find that those rote steps
did not improve my situation significantly. Coupled with the fact that there is
quite often a great deal of additional searching for solutions which yield
diminishing returns, my hope is to grow a tool which covers the majority of
use cases and which can yield results across a variety of system
configurations.

Tersely: An increasingly-comprehensive Python script to diagnose C-state power
management issues on Linux systems.

## Features

Checks 10 key areas that can prevent deep C-states:

1. **PCIe ASPM** - Active State Power Management configuration
2. **CPU C-States** - C-state availability and usage
3. **Wakeup Sources** - High-frequency interrupts and wakeup events
4. **USB Autosuspend** - USB device power management
5. **Kernel Parameters** - Boot parameters affecting power management
6. **Runtime PM** - Device runtime power management
7. **GPU Power** - AMD/Intel/NVIDIA GPU power settings
8. **Network Power** - Network device power management
9. **Thermal Status** - Temperature and throttling checks
10. **CPU Frequency** - Frequency scaling governor and settings

## Usage

For those who live on the edge and trust random other people on the Internet:

```bash
wget -O - https://raw.githubusercontent.com/thomas-p-wilson/cstate-util/refs/heads/master/cstate_checker.py | sudo python3
```

or

```bash
curl https://raw.githubusercontent.com/thomas-p-wilson/cstate-util/refs/heads/master/cstate_checker.py | sudo python3
```

PLEASE NOTE: You can run without root privileges, but it won't be able to check
_everything_. As with everything you get on the Internet, use it at your own
risk!


## Requirements

- Python 3.6+
- Linux system with sysfs
- Root access recommended for complete diagnostics

## Output

The script provides:
- Color-coded status (OK/WARNING/ERROR/INFO)
- Detailed explanations of issues
- Actionable recommendations for fixes
- Summary statistics

## Example Output

```
C-State Diagnostic Tool
==================================================

[OK] PCIe ASPM
  ASPM configured correctly

[WARNING] USB Autosuspend
  USB autosuspend issues
  
  Recommendations:
    • Enable USB autosuspend: echo 'auto' > /sys/bus/usb/devices/*/power/control

Summary
==================================================
OK: 7  WARNING: 2  ERROR: 0  INFO: 1
```

## Common Issues and Fixes

### ASPM Disabled
Add to kernel boot parameters: `pcie_aspm=force`

### C-States Disabled in BIOS
Enable C-states in BIOS/UEFI settings

### USB Devices Preventing Sleep
```bash
# Enable autosuspend for all USB devices
for dev in /sys/bus/usb/devices/*/power/control; do
    echo auto > "$dev"
done
```

### Wrong CPU Governor
```bash
# Set powersave governor
echo powersave | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
```

## License

MIT License - feel free to use and modify