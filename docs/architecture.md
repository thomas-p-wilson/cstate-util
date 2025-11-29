# C-State Diagnostic Script - Technical Specification

## Check Modules

### 1. PCIe ASPM
- `/sys/module/pcie_aspm/parameters/policy`
- `/sys/bus/pci/devices/*/link/l1_aspm`
- `/sys/bus/pci/devices/*/link/clkpm`

### 2. CPU C-States
- `/sys/devices/system/cpu/cpu*/cpuidle/state*/disable`
- `/sys/devices/system/cpu/cpu*/cpuidle/state*/usage`
- `/sys/devices/system/cpu/cpu*/cpuidle/state*/time`
- `/sys/devices/system/cpu/cpu*/cpuidle/state*/residency`

### 3. Wakeup Sources
- `/proc/interrupts` (high-frequency IRQs)
- `/sys/kernel/debug/wakeup_sources` (requires root)
- `/sys/devices/.../power/wakeup_count`

### 4. USB Autosuspend
- `/sys/bus/usb/devices/*/power/control`
- `/sys/bus/usb/devices/*/power/autosuspend_delay_ms`

### 5. Kernel Parameters
- `/proc/cmdline` (intel_idle.max_cstate, processor.max_cstate)
- `/sys/module/intel_idle/parameters/max_cstate`

### 6. Runtime PM
- `/sys/devices/.../power/control` (should be "auto")
- `/sys/devices/.../power/runtime_status`

### 7. GPU Power Management
- `/sys/class/drm/card*/device/power_dpm_state` (AMD)
- `/sys/class/drm/card*/device/power_dpm_force_performance_level` (AMD)
- `/sys/kernel/debug/dri/*/i915_runtime_pm_status` (Intel)
- `/proc/driver/nvidia/gpus/*/power` (NVIDIA)

### 8. Network Device Power
- `/sys/class/net/*/device/power/control`
- `/sys/class/net/*/device/power/runtime_status`
- Check Wake-on-LAN: `ethtool <interface>` (WOL settings)

### 9. Thermal Throttling
- `/sys/class/thermal/thermal_zone*/temp`
- `/sys/class/thermal/thermal_zone*/trip_point_*_temp`
- `/sys/devices/system/cpu/cpu*/thermal_throttle/*`

### 10. CPU Frequency Scaling
- `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`
- `/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq`
- `/sys/devices/system/cpu/cpu*/cpufreq/scaling_min_freq`
- `/sys/devices/system/cpu/cpu*/cpufreq/scaling_max_freq`
- `/sys/devices/system/cpu/intel_pstate/status` (Intel)

## Script Architecture

```
cstate_checker.py
├── CheckResult class (status, message, details)
├── Check functions
│   ├── check_pcie_aspm()
│   ├── check_cpu_cstates()
│   ├── check_wakeup_sources()
│   ├── check_usb_autosuspend()
│   ├── check_kernel_params()
│   ├── check_runtime_pm()
│   ├── check_gpu_power()
│   ├── check_network_power()
│   ├── check_thermal_throttling()
│   └── check_cpu_frequency()
├── Utilities
│   ├── read_sysfs_safe()
│   ├── glob_sysfs()
│   ├── check_root_access()
│   └── format_report()
└── main()
```
