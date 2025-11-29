#!/usr/bin/env python3
"""
C-State Diagnostic Tool for Linux
Checks system configuration for issues preventing deep C-state power levels
"""

import os
import glob
import argparse
import json
import traceback
import time
import subprocess
from enum import Enum
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any, Tuple


class Status(Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"
    INFO = "INFO"


@dataclass
class CheckResult:
    name: str
    status: Status
    message: str
    details: List[str]
    recommendations: List[str]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['status'] = self.status.value
        return d


class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

    @staticmethod
    def disable():
        Colors.GREEN = ''
        Colors.YELLOW = ''
        Colors.RED = ''
        Colors.BLUE = ''
        Colors.RESET = ''
        Colors.BOLD = ''


def read_sysfs_safe(path: str) -> Optional[str]:
    """Safely read a sysfs file, returning None if not accessible"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def glob_sysfs(pattern: str) -> List[str]:
    """Safely glob sysfs paths"""
    try:
        return sorted(glob.glob(pattern))
    except Exception:
        return []


def check_root_access() -> bool:
    """Check if running with root privileges"""
    return os.geteuid() == 0


def check_pcie_aspm() -> CheckResult:
    """Check PCIe Active State Power Management configuration"""
    details = []
    recommendations = []
    status = Status.OK

    # Check ASPM policy
    policy_path = "/sys/module/pcie_aspm/parameters/policy"
    policy = read_sysfs_safe(policy_path)
    
    if policy is None:
        return CheckResult(
            name="PCIe ASPM",
            status=Status.ERROR,
            message="ASPM not available (kernel config issue)",
            details=[],
            recommendations=["Recompile kernel with CONFIG_PCIEASPM=y"]
        )
    
    details.append(f"Current policy: {policy}")
    
    if "powersupersave" not in policy:
        status = Status.WARNING
        recommendations.append(
            "Set ASPM policy to 'powersupersave'"
        )
    
    # Parse lspci output to check ASPM state on all devices
    disabled_devices = []
    try:
        result = subprocess.run(
            ['lspci', '-vv'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            current_device = None
            current_desc = None
            for line in result.stdout.split('\n'):
                # Match device ID line (e.g., "00:01.0 PCI bridge: ...")
                if line and not line.startswith('\t'):
                    parts = line.split(None, 1)
                    if parts and ':' in parts[0] and '.' in parts[0]:
                        # Store short format for setpci command
                        dev_id = parts[0]
                        current_device = dev_id
                        current_desc = parts[1] if len(parts) > 1 else "unknown"
                # Check for ASPM disabled in LnkCtl line
                elif current_device and '\tLnkCtl:' in line:
                    if 'ASPM Disabled' in line or 'ASPM L0s L1 Disabled' in line:
                        # Extract device type from description
                        dev_type = current_desc.split(':')[0] if ':' in current_desc else current_desc
                        disabled_devices.append((current_device, dev_type))
        else:
            details.append("Warning: lspci command failed")
    except FileNotFoundError:
        return CheckResult(
            name="PCIe ASPM",
            status=Status.ERROR,
            message="lspci command not found",
            details=["Install pciutils package to check ASPM status"],
            recommendations=["Install pciutils: apt install pciutils / dnf install pciutils"]
        )
    except subprocess.TimeoutExpired:
        details.append("Warning: lspci command timed out")
    except Exception as e:
        details.append(f"Warning: Could not parse lspci output: {e}")
    
    if disabled_devices:
        status = Status.WARNING
        details.append(f"Devices with ASPM disabled: {len(disabled_devices)}")
        for dev_id, dev_type in disabled_devices[:10]:
            details.append(f"  - {dev_id} ({dev_type})")
        if len(disabled_devices) > 10:
            details.append(f"  ... and {len(disabled_devices) - 10} more")
        
        recommendations.append("To enable ASPM on these devices, run the following commands as root:")
        recommendations.append("")
        for dev_id, dev_type in disabled_devices[:10]:
            # setpci command to enable L0s and L1 ASPM
            # CAP_EXP+10.w is the Link Control register
            # Bits 0-1 control ASPM: 00=Disabled, 01=L0s, 10=L1, 11=L0s+L1
            recommendations.append(f"  # Enable ASPM L0s+L1 for {dev_id} ({dev_type})")
            recommendations.append(f"  setpci -s {dev_id} CAP_EXP+10.w=0003")
        if len(disabled_devices) > 10:
            recommendations.append(f"  # ... and {len(disabled_devices) - 10} more devices")
        recommendations.append("")
        recommendations.append("Note: Changes made with setpci are not persistent across reboots")
        recommendations.append("For permanent fix, use 'pcie_aspm=force' boot parameter or check device drivers")
    
    message = (
        "ASPM configured correctly"
        if status == Status.OK
        else "ASPM issues detected"
    )
    
    return CheckResult(
        name="PCIe ASPM",
        status=status,
        message=message,
        details=details,
        recommendations=recommendations
    )


def check_cpu_cstates() -> CheckResult:
    """Check CPU C-state configuration and usage"""
    details = []
    recommendations = []
    status = Status.OK

    cpu_dirs = glob_sysfs("/sys/devices/system/cpu/cpu[0-9]*/cpuidle")
    if not cpu_dirs:
        return CheckResult(
            name="CPU C-States",
            status=Status.ERROR,
            message="cpuidle not available",
            details=[],
            recommendations=["Check if cpuidle is enabled in kernel"]
        )
    
    # Check first CPU as representative
    cpu0_idle = "/sys/devices/system/cpu/cpu0/cpuidle"
    state_dirs = glob_sysfs(f"{cpu0_idle}/state*")
    
    if not state_dirs:
        return CheckResult(
            name="CPU C-States",
            status=Status.ERROR,
            message="No C-states available",
            details=[],
            recommendations=["Check CPU and BIOS C-state settings"]
        )
    
    details.append(f"Available C-states: {len(state_dirs)}")
    
    disabled_states = []
    deepest_enabled = None
    
    for state_dir in state_dirs:
        state_name = os.path.basename(state_dir)
        disabled = read_sysfs_safe(f"{state_dir}/disable")
        name = read_sysfs_safe(f"{state_dir}/name")
        
        if disabled == "1":
            disabled_states.append(f"{state_name} ({name})")
        else:
            deepest_enabled = name
    
    if disabled_states:
        status = Status.WARNING
        details.append(f"Disabled states: {', '.join(disabled_states)}")
        recommendations.append("Enable all C-states for maximum power savings")
    
    if deepest_enabled:
        details.append(f"Deepest enabled state: {deepest_enabled}")
        if deepest_enabled in ["POLL", "C1"]:
            status = Status.ERROR
            recommendations.append(
                "Only shallow C-states enabled - check BIOS settings"
            )
    
    # Check usage statistics
    usage = read_sysfs_safe(f"{cpu0_idle}/state1/usage")
    if usage and int(usage) == 0:
        status = Status.WARNING
        details.append("C-states not being used")
        recommendations.append(
            "System may be too busy or C-states blocked by devices"
        )
    
    message = (
        "C-states configured correctly"
        if status == Status.OK
        else "C-state issues detected"
    )
    
    return CheckResult(
        name="CPU C-States",
        status=status,
        message=message,
        details=details,
        recommendations=recommendations
    )


def check_wakeup_sources() -> CheckResult:
    """Check for frequent wakeup sources with detailed interrupt analysis"""
    details = []
    recommendations = []
    status = Status.OK

    # Interrupt categories for classification
    INTERRUPT_CATEGORIES = {
        'function_call': ['CAL', 'TLB', 'RES', 'IPI'],
        'timer': ['LOC', 'timer', 'hrtimer'],
        'io': ['ahci', 'nvme', 'xhci', 'i8042', 'sata'],
        'network': ['eth', 'wlan', 'enp', 'wlp', 'eno'],
        'gpu': ['i915', 'amdgpu', 'nvidia'],
    }

    def parse_interrupts() -> Dict[str, Tuple[int, List[int]]]:
        """Parse /proc/interrupts and return interrupt counts per IRQ"""
        irq_data = {}
        try:
            with open("/proc/interrupts", 'r') as f:
                lines = f.readlines()
            
            # Get number of CPUs from header
            num_cpus = len(lines[0].split()) - 1
            
            for line in lines[1:]:
                parts = line.split()
                if len(parts) < num_cpus + 2:
                    continue
                
                irq_name = parts[-1]
                try:
                    # Get counts for each CPU
                    cpu_counts = [int(parts[i]) for i in range(1, num_cpus + 1)]
                    total = sum(cpu_counts)
                    irq_data[irq_name] = (total, cpu_counts)
                except (ValueError, IndexError):
                    continue
        except Exception:
            pass
        
        return irq_data

    def categorize_interrupt(irq_name: str) -> str:
        """Categorize an interrupt by its name"""
        irq_lower = irq_name.lower()
        for category, patterns in INTERRUPT_CATEGORIES.items():
            for pattern in patterns:
                if pattern.lower() in irq_lower:
                    return category
        return 'other'

    # Check /proc/interrupts for high-frequency IRQs with rate calculation
    try:
        # Read interrupts twice to calculate rate
        irq_data_1 = parse_interrupts()
        time.sleep(1)
        irq_data_2 = parse_interrupts()
        
        if not irq_data_1 or not irq_data_2:
            details.append("Could not read interrupt data")
        else:
            # Calculate rates (interrupts per second)
            irq_rates = {}
            for irq_name in irq_data_2:
                if irq_name in irq_data_1:
                    rate = irq_data_2[irq_name][0] - irq_data_1[irq_name][0]
                    cpu_rates = [
                        irq_data_2[irq_name][1][i] - irq_data_1[irq_name][1][i]
                        for i in range(len(irq_data_2[irq_name][1]))
                    ]
                    irq_rates[irq_name] = (rate, cpu_rates)
            
            # Categorize all interrupts
            categorized_irqs = {cat: [] for cat in INTERRUPT_CATEGORIES.keys()}
            categorized_irqs['other'] = []
            
            for irq_name, (rate, cpu_rates) in irq_rates.items():
                category = categorize_interrupt(irq_name)
                avg_per_cpu = rate / len(cpu_rates) if cpu_rates else rate
                categorized_irqs[category].append((irq_name, rate, avg_per_cpu))
            
            # Sort each category by rate
            for category in categorized_irqs:
                categorized_irqs[category].sort(key=lambda x: x[1], reverse=True)
            
            # Collect all interrupts and determine what to show
            all_irqs = []
            for category in categorized_irqs.values():
                all_irqs.extend(category)
            all_irqs.sort(key=lambda x: x[1], reverse=True)
            
            HIGH_RATE_THRESHOLD = 1000  # interrupts per second
            
            # Get interrupts above threshold
            irqs_above_threshold = [irq for irq in all_irqs if irq[1] > HIGH_RATE_THRESHOLD]
            
            # Show either top 3 or all above threshold, whichever is more
            irqs_to_show = irqs_above_threshold if len(irqs_above_threshold) >= 3 else all_irqs[:3]
            
            if irqs_to_show:
                details.append(f"Top interrupt sources (showing {len(irqs_to_show)}):")
                for irq, rate, avg_per_cpu in irqs_to_show:
                    category = categorize_interrupt(irq)
                    total_count_2 = irq_data_2.get(irq, (0, []))[0]
                    details.append(
                        f"  - {irq} ({category}): {rate:,}/sec "
                        f"(total: {total_count_2:,}, avg: {avg_per_cpu:,.0f}/sec per CPU)"
                    )
                details.append("")
            
            # Check for high-rate function call interrupts specifically
            high_function_call_irqs = [
                irq for irq in categorized_irqs['function_call']
                if irq[1] > HIGH_RATE_THRESHOLD
            ]
            
            if high_function_call_irqs:
                status = Status.WARNING
                details.append("⚠️  High function call interrupt rate detected:")
                for irq, rate, avg_per_cpu in high_function_call_irqs[:3]:
                    details.append(f"  - {irq}: {rate:,}/sec ({avg_per_cpu:,.0f}/sec per CPU)")
                details.append("")
                details.append("Function call interrupts prevent deep C-states")
                
                recommendations.append(
                    "Function call interrupts indicate frequent CPU wakeups. Common causes:"
                )
                recommendations.append(
                    "  • Busy kernel threads - check with: ps -eLo pid,tid,comm,state | grep ' R '"
                )
                recommendations.append(
                    "  • High-frequency timers - check: cat /proc/timer_list | grep -A5 'expires at'"
                )
                recommendations.append(
                    "  • Workqueue activity - check: cat /sys/kernel/debug/workqueue/workqueues"
                )
            
            # Check for other high-rate interrupts
            other_high_irqs = []
            for category in ['timer', 'io', 'network', 'gpu', 'other']:
                high_in_category = [
                    irq for irq in categorized_irqs[category]
                    if irq[1] > HIGH_RATE_THRESHOLD
                ]
                other_high_irqs.extend(high_in_category)
            
            if other_high_irqs:
                if not high_function_call_irqs:
                    status = Status.WARNING
                details.append("Other high-frequency interrupts:")
                for irq, rate, _ in other_high_irqs[:5]:
                    category = categorize_interrupt(irq)
                    details.append(f"  - {irq} ({category}): {rate:,}/sec")
                if len(other_high_irqs) > 5:
                    details.append(f"  ... and {len(other_high_irqs) - 5} more")
                
                # Add category-specific recommendations
                high_io = [irq for irq in categorized_irqs['io'] if irq[1] > HIGH_RATE_THRESHOLD]
                high_network = [irq for irq in categorized_irqs['network'] if irq[1] > HIGH_RATE_THRESHOLD]
                
                if high_io:
                    recommendations.append(
                        "High I/O interrupt rate may indicate heavy disk/storage activity"
                    )
                if high_network:
                    recommendations.append(
                        "High network interrupt rate - consider interrupt coalescing or RSS tuning"
                    )
            
            # Try to identify busy kernel threads if we have high function call interrupts
            if high_function_call_irqs:
                try:
                    result = subprocess.run(
                        ['ps', '-eLo', 'comm', '--sort=-time', '--no-headers'],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    if result.returncode == 0:
                        busy_threads = [
                            t.strip() for t in result.stdout.split('\n')[:5]
                            if t.strip()
                        ]
                        if busy_threads:
                            details.append("")
                            details.append("Top CPU-consuming threads:")
                            for thread in busy_threads:
                                details.append(f"  - {thread}")
                except Exception:
                    pass
            
            # Add advanced profiling recommendation
            if status == Status.WARNING:
                recommendations.append(
                    "For detailed interrupt analysis, run:\n"
                    "  perf record -e 'irq:*' -a -g sleep 10 && perf report"
                )
    
    except Exception as e:
        details.append(f"Could not analyze interrupts: {e}")
    
    # Check wakeup_sources (requires root)
    wakeup_path = "/sys/kernel/debug/wakeup_sources"
    if check_root_access():
        wakeup_data = read_sysfs_safe(wakeup_path)
        if wakeup_data:
            try:
                lines = wakeup_data.split('\n')[1:]  # Skip header
                active_sources = []
                for line in lines:
                    if line.strip():
                        parts = line.split()
                        if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) > 0:
                            active_sources.append(parts[0])
                
                if active_sources:
                    if details:
                        details.append("")
                    details.append(f"Active wakeup sources: {len(active_sources)}")
                    details.extend([f"  - {src}" for src in active_sources[:5]])
                    if len(active_sources) > 5:
                        details.append(f"  ... and {len(active_sources) - 5} more")
            except Exception:
                pass
        else:
            details.append("Could not read wakeup_sources (mount debugfs)")
    else:
        if details:
            details.append("")
        details.append("Run as root to check wakeup_sources")
        if status == Status.OK:
            status = Status.INFO
    
    message = (
        "No excessive wakeup sources"
        if status == Status.OK
        else "High-frequency wakeup sources detected"
    )
    
    return CheckResult(
        name="Wakeup Sources",
        status=status,
        message=message,
        details=details,
        recommendations=recommendations
    )


def check_usb_autosuspend() -> CheckResult:
    """Check USB device autosuspend configuration"""
    details = []
    recommendations = []
    status = Status.OK

    usb_devices = glob_sysfs("/sys/bus/usb/devices/*/power/control")
    
    if not usb_devices:
        return CheckResult(
            name="USB Autosuspend",
            status=Status.INFO,
            message="No USB devices found",
            details=[],
            recommendations=[]
        )
    
    disabled_devices = []
    for control_path in usb_devices:
        control = read_sysfs_safe(control_path)
        if control == "on":
            device = control_path.split('/')[5]
            # Try to get device name
            product_path = control_path.replace("/power/control", "/product")
            product = read_sysfs_safe(product_path) or device
            disabled_devices.append(f"{device} ({product})")
    
    details.append(f"Total USB devices: {len(usb_devices)}")
    
    if disabled_devices:
        status = Status.WARNING
        details.append(
            f"Devices with autosuspend disabled: {len(disabled_devices)}"
        )
        details.extend([f"  - {dev}" for dev in disabled_devices[:5]])
        if len(disabled_devices) > 5:
            details.append(f"  ... and {len(disabled_devices) - 5} more")
        recommendations.append(
            "Enable USB autosuspend: "
            "echo 'auto' > /sys/bus/usb/devices/*/power/control"
        )
    else:
        details.append("All devices have autosuspend enabled")
    
    message = (
        "USB autosuspend configured correctly"
        if status == Status.OK
        else "USB autosuspend issues"
    )
    
    return CheckResult(
        name="USB Autosuspend",
        status=status,
        message=message,
        details=details,
        recommendations=recommendations
    )


def check_kernel_params() -> CheckResult:
    """Check kernel boot parameters related to power management"""
    details = []
    recommendations = []
    status = Status.OK

    cmdline = read_sysfs_safe("/proc/cmdline")
    if not cmdline:
        return CheckResult(
            name="Kernel Parameters",
            status=Status.ERROR,
            message="Could not read kernel command line",
            details=[],
            recommendations=[]
        )
    
    details.append(f"Command line: {cmdline[:100]}...")
    
    # Check for power-related parameters
    power_params = {
        "intel_idle.max_cstate": None,
        "processor.max_cstate": None,
        "pcie_aspm": None,
        "pcie_aspm.policy": None,
        "intel_pstate": None,
    }
    
    # Split cmdline by spaces and parse each parameter
    for param_entry in cmdline.split():
        if '=' in param_entry:
            key, value = param_entry.split('=', 1)
            if key in power_params:
                power_params[key] = value
                details.append(f"{key}: {value}")
        else:
            # Boolean flag without value
            if param_entry in power_params:
                power_params[param_entry] = "present"
                details.append(f"{param_entry}: present")
    
    # Check max_cstate restrictions
    if power_params["intel_idle.max_cstate"]:
        try:
            max_cstate = int(power_params["intel_idle.max_cstate"])
            if max_cstate < 6:
                status = Status.WARNING
                recommendations.append(
                    f"intel_idle.max_cstate={max_cstate} "
                    "limits deep C-states"
                )
        except ValueError:
            pass
    
    # Check ASPM
    if power_params["pcie_aspm"] == "off":
        status = Status.ERROR
        recommendations.append(
            "ASPM is disabled - remove 'pcie_aspm=off' "
            "from boot parameters"
        )
    elif not power_params["pcie_aspm"]:
        recommendations.append(
            "Consider adding 'pcie_aspm=force' to boot parameters"
        )
    
    # Check ASPM policy parameter
    if power_params["pcie_aspm.policy"]:
        policy_value = power_params["pcie_aspm.policy"]
        if policy_value != "powersupersave":
            status = Status.WARNING
            recommendations.append(
                f"pcie_aspm.policy={policy_value} - "
                "set to 'powersupersave' for maximum power savings"
            )
    
    message = (
        "Kernel parameters OK"
        if status == Status.OK
        else "Kernel parameter issues"
    )
    
    return CheckResult(
        name="Kernel Parameters",
        status=status,
        message=message,
        details=details,
        recommendations=recommendations
    )


def check_runtime_pm() -> CheckResult:
    """Check runtime power management for devices"""
    details = []
    recommendations = []
    status = Status.OK

    # Check various device classes
    device_patterns = [
        "/sys/class/net/*/device/power/control",
        "/sys/class/sound/card*/device/power/control",
        "/sys/class/scsi_host/*/power/control",
    ]
    
    disabled_devices = []
    total_devices = 0
    
    for pattern in device_patterns:
        for control_path in glob_sysfs(pattern):
            total_devices += 1
            control = read_sysfs_safe(control_path)
            if control == "on":
                device = control_path.split('/')
                device_name = (
                    f"{device[3]}/{device[4]}"
                    if len(device) > 4
                    else control_path
                )
                disabled_devices.append(device_name)
    
    details.append(f"Checked devices: {total_devices}")
    
    if disabled_devices:
        status = Status.WARNING
        details.append(f"Devices without runtime PM: {len(disabled_devices)}")
        details.extend([f"  - {dev}" for dev in disabled_devices[:5]])
        if len(disabled_devices) > 5:
            details.append(f"  ... and {len(disabled_devices) - 5} more")
        recommendations.append(
            "Enable runtime PM: echo 'auto' > /sys/.../power/control"
        )
    else:
        details.append("All devices have runtime PM enabled")
    
    message = (
        "Runtime PM configured correctly"
        if status == Status.OK
        else "Runtime PM issues"
    )
    
    return CheckResult(
        name="Runtime PM",
        status=status,
        message=message,
        details=details,
        recommendations=recommendations
    )


def check_gpu_power() -> CheckResult:
    """Check GPU power management settings"""
    details = []
    recommendations = []
    status = Status.OK

    # Check AMD GPU
    amd_paths = glob_sysfs("/sys/class/drm/card*/device/power_dpm_state")
    if amd_paths:
        for path in amd_paths:
            state = read_sysfs_safe(path)
            card = path.split('/')[5]
            details.append(f"AMD {card} DPM state: {state}")
            if state != "balanced" and state != "battery":
                status = Status.WARNING
                recommendations.append(
                    f"Set AMD GPU to power-saving mode: "
                    f"echo 'battery' > {path}"
                )
    
    # Check Intel GPU
    intel_paths = glob_sysfs("/sys/kernel/debug/dri/*/i915_runtime_pm_status")
    if intel_paths and check_root_access():
        for path in intel_paths:
            pm_status = read_sysfs_safe(path)
            if pm_status:
                pm_enabled = (
                    'enabled' if 'Runtime' in pm_status
                    else 'check manually'
                )
                details.append(f"Intel GPU runtime PM: {pm_enabled}")
    
    # Check NVIDIA (if present)
    nvidia_paths = glob_sysfs("/proc/driver/nvidia/gpus/*/power")
    if nvidia_paths:
        details.append(
            "NVIDIA GPU detected - check nvidia-smi for power settings"
        )
        recommendations.append(
            "Configure NVIDIA power management via nvidia-settings"
        )
    
    if not amd_paths and not intel_paths and not nvidia_paths:
        return CheckResult(
            name="GPU Power Management",
            status=Status.INFO,
            message="No GPU detected or drivers not loaded",
            details=[],
            recommendations=[]
        )
    
    message = (
        "GPU power management OK"
        if status == Status.OK
        else "GPU power issues"
    )
    
    return CheckResult(
        name="GPU Power Management",
        status=status,
        message=message,
        details=details,
        recommendations=recommendations
    )


def check_network_power() -> CheckResult:
    """Check network device power management"""
    details = []
    recommendations = []
    status = Status.OK

    net_devices = glob_sysfs("/sys/class/net/*/device/power/control")
    
    if not net_devices:
        return CheckResult(
            name="Network Power",
            status=Status.INFO,
            message="No network devices found",
            details=[],
            recommendations=[]
        )
    
    disabled_devices = []
    for control_path in net_devices:
        control = read_sysfs_safe(control_path)
        if control == "on":
            interface = control_path.split('/')[4]
            disabled_devices.append(interface)
    
    details.append(f"Network interfaces: {len(net_devices)}")
    
    if disabled_devices:
        status = Status.WARNING
        details.append(
            f"Interfaces without power management: "
            f"{', '.join(disabled_devices)}"
        )
        recommendations.append("Enable network device power management")
        recommendations.append(
            "Disable Wake-on-LAN if not needed: "
            "ethtool -s <interface> wol d"
        )
    else:
        details.append("All interfaces have power management enabled")
    
    message = (
        "Network power management OK"
        if status == Status.OK
        else "Network power issues"
    )
    
    return CheckResult(
        name="Network Power",
        status=status,
        message=message,
        details=details,
        recommendations=recommendations
    )


def check_thermal_throttling() -> CheckResult:
    """Check for thermal throttling issues"""
    details = []
    recommendations = []
    status = Status.OK

    # Check thermal zones
    thermal_zones = glob_sysfs("/sys/class/thermal/thermal_zone*")
    
    if not thermal_zones:
        return CheckResult(
            name="Thermal Status",
            status=Status.INFO,
            message="No thermal zones found",
            details=[],
            recommendations=[]
        )
    
    hot_zones = []
    for zone_path in thermal_zones:
        temp_path = f"{zone_path}/temp"
        type_path = f"{zone_path}/type"
        
        temp = read_sysfs_safe(temp_path)
        zone_type = read_sysfs_safe(type_path) or "unknown"
        
        if temp:
            temp_c = int(temp) / 1000
            details.append(f"{zone_type}: {temp_c:.1f}°C")
            
            if temp_c > 80:
                hot_zones.append((zone_type, temp_c))
                status = Status.WARNING
    
    if hot_zones:
        recommendations.append("High temperatures detected - check cooling")
        recommendations.append("Thermal throttling may prevent deep C-states")
    
    # Check CPU throttle events
    throttle_paths = glob_sysfs(
        "/sys/devices/system/cpu/cpu*/thermal_throttle/core_throttle_count"
    )
    if throttle_paths:
        throttle_count = read_sysfs_safe(throttle_paths[0])
        if throttle_count and int(throttle_count) > 0:
            details.append(f"CPU throttle events: {throttle_count}")
            status = Status.WARNING
            recommendations.append("CPU has been thermally throttled")
    
    message = (
        "No thermal issues"
        if status == Status.OK
        else "Thermal issues detected"
    )
    
    return CheckResult(
        name="Thermal Status",
        status=status,
        message=message,
        details=details,
        recommendations=recommendations
    )


def check_cpu_frequency() -> CheckResult:
    """Check CPU frequency scaling configuration"""
    details = []
    recommendations = []
    status = Status.OK

    # Check scaling governor
    governor_path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
    governor = read_sysfs_safe(governor_path)
    
    if not governor:
        return CheckResult(
            name="CPU Frequency Scaling",
            status=Status.INFO,
            message="cpufreq not available",
            details=[],
            recommendations=[]
        )
    
    details.append(f"Scaling governor: {governor}")
    
    if governor not in ["powersave", "schedutil"]:
        status = Status.WARNING
        recommendations.append(
            f"Governor '{governor}' may prevent deep C-states"
        )
        recommendations.append(
            "Use 'powersave' or 'schedutil' governor "
            "for better power management"
        )
    
    # Check frequency limits
    cur_freq = read_sysfs_safe(
        "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
    )
    min_freq = read_sysfs_safe(
        "/sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq"
    )
    max_freq = read_sysfs_safe(
        "/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq"
    )
    
    if cur_freq and min_freq and max_freq:
        cur_mhz = int(cur_freq) / 1000
        min_mhz = int(min_freq) / 1000
        max_mhz = int(max_freq) / 1000
        details.append(
            f"Frequency: {cur_mhz:.0f} MHz "
            f"(min: {min_mhz:.0f}, max: {max_mhz:.0f})"
        )
    
    # Check Intel P-state
    pstate_status = read_sysfs_safe(
        "/sys/devices/system/cpu/intel_pstate/status"
    )
    if pstate_status:
        details.append(f"Intel P-state: {pstate_status}")
        if pstate_status == "off":
            status = Status.WARNING
            recommendations.append("Intel P-state is disabled")
    
    message = (
        "CPU frequency scaling OK"
        if status == Status.OK
        else "Frequency scaling issues"
    )
    
    return CheckResult(
        name="CPU Frequency Scaling",
        status=status,
        message=message,
        details=details,
        recommendations=recommendations
    )


def format_result(result: CheckResult, verbose: bool = False) -> str:
    """Format a check result for display"""
    status_colors = {
        Status.OK: Colors.GREEN,
        Status.WARNING: Colors.YELLOW,
        Status.ERROR: Colors.RED,
        Status.INFO: Colors.BLUE,
    }
    
    color = status_colors[result.status]
    output = (
        f"\n{Colors.BOLD}[{color}{result.status.value}{Colors.RESET}"
        f"{Colors.BOLD}] {result.name}{Colors.RESET}\n"
    )
    output += f"  {result.message}\n"
    
    if verbose and result.details:
        output += f"\n  {Colors.BOLD}Details:{Colors.RESET}\n"
        for detail in result.details:
            output += f"    {detail}\n"
    
    if result.recommendations:
        output += f"\n  {Colors.BOLD}Recommendations:{Colors.RESET}\n"
        for rec in result.recommendations:
            output += f"    • {rec}\n"
    
    return output


def main():
    parser = argparse.ArgumentParser(
        description="Diagnose C-state power management issues on Linux",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed information"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output"
    )
    
    args = parser.parse_args()
    
    if args.no_color or args.json:
        Colors.disable()
    
    if not args.json:
        print(f"{Colors.BOLD}C-State Diagnostic Tool{Colors.RESET}")
        print("=" * 50)
        
        if not check_root_access():
            print(
                f"{Colors.YELLOW}Note: Running without root - "
                f"some checks will be limited{Colors.RESET}"
            )
    
    # Run all checks
    checks = [
        check_pcie_aspm,
        check_cpu_cstates,
        check_wakeup_sources,
        check_usb_autosuspend,
        check_kernel_params,
        check_runtime_pm,
        check_gpu_power,
        check_network_power,
        check_thermal_throttling,
        check_cpu_frequency,
    ]
    
    results = []
    for check_func in checks:
        try:
            result = check_func()
            results.append(result)
        except Exception as e:
            func_name = (
                check_func.__name__
                .replace("check_", "")
                .replace("_", " ")
                .title()
            )
            results.append(CheckResult(
                name=func_name,
                status=Status.ERROR,
                message=f"Check failed: {str(e)}",
                details=[],
                recommendations=[]
            ))
    
    # Output results
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        for result in results:
            print(format_result(result, args.verbose))
        
        # Summary
        print(f"\n{Colors.BOLD}Summary{Colors.RESET}")
        print("=" * 50)
        
        status_counts = {s: 0 for s in Status}
        for result in results:
            status_counts[result.status] += 1
        
        print(
            f"{Colors.GREEN}OK: {status_counts[Status.OK]}{Colors.RESET}  "
            f"{Colors.YELLOW}WARNING: {status_counts[Status.WARNING]}"
            f"{Colors.RESET}  "
            f"{Colors.RED}ERROR: {status_counts[Status.ERROR]}{Colors.RESET}  "
            f"{Colors.BLUE}INFO: {status_counts[Status.INFO]}{Colors.RESET}"
        )
        
        if (status_counts[Status.ERROR] > 0 or
                status_counts[Status.WARNING] > 0):
            print(
                f"\n{Colors.YELLOW}Issues detected that may prevent "
                f"deep C-states.{Colors.RESET}"
            )
            print("Review recommendations above for fixes.")
        else:
            print(f"\n{Colors.GREEN}No major issues detected!{Colors.RESET}")


if __name__ == "__main__":
    main()