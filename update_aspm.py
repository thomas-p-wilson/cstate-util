#!/usr/bin/env python3
# Copyright (c) 2010-2013 Luis R. Rodriguez <mcgrof@do-not-panic.com>
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

"""
ASPM Tuning script

This script lets you enable ASPM on your devices in case your BIOS
does not have it enabled for some reason. If your BIOS does not have
it enabled it is usually for a good reason so you should only use this if
you know what you are doing. Typically you would only need to enable
ASPM manually when doing development and using a card that typically
is not present on a laptop, or using the cardbus slot. The BIOS typically
disables ASPM for foreign cards and on the cardbus slot. Check also
if you may need to do other things than what is below on your vendor
documentation.

To use this script You will need for now to at least query your device
PCI endpoint and root complex addresses using the convention output by
lspci: [<bus>]:[<slot>].[<func>]

For example:

03:00.0 Network controller: Atheros Communications Inc. AR9300 Wireless LAN adaptor (rev 01
00:1c.1 PCI bridge: Intel Corporation 82801H (ICH8 Family) PCI Express Port 2 (rev 03)

The root complex for the endpoint can be found using lspci -t

For more details refer to:

http://wireless.kernel.org/en/users/Documentation/ASPM
"""

import os
import sys
import subprocess
import time
import argparse
from typing import Optional, Tuple


# ANSI color codes
class Colors:
    GREEN = "\033[01;32m"
    YELLOW = "\033[01;33m"
    NORMAL = "\033[00m"
    BLUE = "\033[34m"
    RED = "\033[31m"
    PURPLE = "\033[35m"
    CYAN = "\033[36m"
    UNDERLINE = "\033[02m"


# Configuration - modify this value as needed
# We'll only enable the last 2 bits by using a mask
# of :3 to setpci, this will ensure we keep the existing
# values on the byte.
#
# Hex  Binary  Meaning
# -------------------------
# 0    0b00    L0 only
# 1    0b01    L0s only
# 2    0b10    L1 only
# 3    0b11    L1 and L0s
ASPM_SETTING = 3

# Constants
MAX_SEARCH = 100


def aspm_setting_to_string(setting: int) -> str:
    """Convert ASPM setting value to human-readable string."""
    settings = {
        0: f"\t{Colors.BLUE}L0 only{Colors.NORMAL}, {Colors.RED}ASPM disabled{Colors.NORMAL}",
        1: "",
        2: f"\t{Colors.GREEN}L1 only{Colors.NORMAL}",
        3: f"\t{Colors.GREEN}L1 and L0s{Colors.NORMAL}",
    }
    return settings.get(setting, f"\t{Colors.RED}Invalid{Colors.NORMAL}")


def run_command(cmd: list) -> Tuple[int, str]:
    """Run a shell command and return exit code and output."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode, result.stdout.strip()
    except Exception as e:
        print(f"Error running command {' '.join(cmd)}: {e}")
        return 1, ""


def device_present(device: str, check_type: str = "present") -> bool:
    """Check if a PCI device is present."""
    returncode, output = run_command(["lspci"])
    if returncode != 0:
        return False
    
    present = device in output
    complaint = f"{Colors.RED}not present{Colors.NORMAL}"
    
    if not present:
        if check_type != "present":
            complaint = f"{Colors.RED}disappeared{Colors.NORMAL}"
        
        print(f"Device {Colors.BLUE}{device}{Colors.NORMAL} {complaint}")
        return False
    
    return True


def find_aspm_byte_address(device: str) -> Optional[str]:
    """Find the ASPM byte address for a given device."""
    if not device_present(device, "present"):
        return None
    
    # Get initial search address
    returncode, search = run_command(["setpci", "-s", device, "34.b"])
    if returncode != 0:
        return None
    
    search_count = 1
    
    # We know on the first search $SEARCH will not be
    # 10 but this simplifies the implementation.
    while search != "10" and search_count <= MAX_SEARCH:
        returncode, end_search = run_command(["setpci", "-s", device, f"{search}.b"])
        if returncode != 0:
            return None
        
        # Convert hex to uppercase
        search_upper = search.upper()
        
        if end_search == "10":
            # Calculate ASPM byte address
            search_val = int(search_upper, 16)
            aspm_addr = search_val + 0x10
            return f"{aspm_addr:X}"
        
        # Move to next byte
        search_val = int(search_upper, 16)
        next_addr = search_val + 1
        returncode, search = run_command(["setpci", "-s", device, f"{next_addr:X}.b"])
        if returncode != 0:
            return None
        
        search_count += 1
    
    if search_count >= MAX_SEARCH:
        print(f"Long loop while looking for ASPM word for {device}")
        return None
    
    return None


def get_all_pci_devices() -> list:
    """Get a list of all PCI device addresses."""
    returncode, output = run_command(["lspci"])
    if returncode != 0:
        return []
    
    devices = []
    for line in output.split('\n'):
        if line.strip():
            # Extract device address (first field before space)
            device_addr = line.split()[0]
            devices.append(device_addr)
    
    return devices


def check_aspm_status(device: str) -> Optional[Tuple[str, int, int]]:
    """
    Check ASPM status for a device.
    Returns tuple of (address, current_value, desired_value) or None if not applicable.
    """
    aspm_byte_address = find_aspm_byte_address(device)
    if aspm_byte_address is None:
        return None
    
    # Get current ASPM byte value
    returncode, aspm_byte_hex = run_command(["setpci", "-s", device, f"{aspm_byte_address}.b"])
    if returncode != 0:
        return None
    
    aspm_byte_hex = aspm_byte_hex.upper()
    current_val = int(aspm_byte_hex, 16)
    desired_val = (current_val & ~0x7) | ASPM_SETTING
    
    return (aspm_byte_address, current_val, desired_val)


def enable_aspm_byte(device: str, verbose: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Enable ASPM for a given device.
    Returns tuple of (success, error_message).
    """
    if not device_present(device, "present"):
        return False, f"Device {device} not found"
    
    aspm_byte_address = find_aspm_byte_address(device)
    if aspm_byte_address is None:
        return False, f"No ASPM byte found for {device}"
    
    # Get current ASPM byte value
    returncode, aspm_byte_hex = run_command(["setpci", "-s", device, f"{aspm_byte_address}.b"])
    if returncode != 0:
        return False, f"Failed to get ASPM byte value for {device}"
    
    aspm_byte_hex = aspm_byte_hex.upper()
    current_val = int(aspm_byte_hex, 16)
    desired_val = (current_val & ~0x7) | ASPM_SETTING
    desired_aspm_byte_hex = f"{desired_val:X}"
    
    if aspm_byte_address == "INVALID":
        returncode, device_info = run_command(["lspci", "-s", device])
        return False, f"No ASPM byte could be found for {device_info}"
    
    # Get device description
    returncode, device_info = run_command(["lspci", "-s", device])
    if verbose:
        print(device_info)
        print(f"\t{Colors.YELLOW}0x{aspm_byte_address}{Colors.NORMAL} : "
              f"{Colors.CYAN}0x{aspm_byte_hex}{Colors.GREEN} --> "
              f"{Colors.BLUE}0x{desired_aspm_byte_hex}{Colors.NORMAL} ... ", end="")
    
    if not device_present(device, "present"):
        sys.exit(1)
    
    # Avoid setting if already set
    if aspm_byte_hex == desired_aspm_byte_hex:
        if verbose:
            print(f"[{Colors.GREEN}SUCCESS{Colors.NORMAL}] ({Colors.GREEN}already set{Colors.NORMAL})")
            print(aspm_setting_to_string(ASPM_SETTING))
        return True, None
    
    # This only writes the last 3 bits
    returncode, _ = run_command(["setpci", "-s", device, f"{aspm_byte_address}.b={ASPM_SETTING}:3"])
    if returncode != 0:
        if verbose:
            print(f"\t[{Colors.RED}FAIL{Colors.NORMAL}]")
        return False, f"Failed to write ASPM setting to {device}"
    
    time.sleep(1)  # Reduced sleep time for batch operations
    
    # Verify the setting
    returncode, actual_aspm_byte_hex = run_command(["setpci", "-s", device, f"{aspm_byte_address}.b"])
    if returncode != 0:
        if verbose:
            print(f"\t[{Colors.RED}FAIL{Colors.NORMAL}]")
        return False, f"Failed to verify ASPM setting for {device}"
    
    actual_aspm_byte_hex = actual_aspm_byte_hex.upper()
    
    # Do not retry this if it failed, if it failed to set.
    # Likely if it failed its a good reason and you should look
    # into that.
    if actual_aspm_byte_hex != desired_aspm_byte_hex:
        if verbose:
            print(f"\t[{Colors.RED}FAIL{Colors.NORMAL}] (0x{actual_aspm_byte_hex})")
        return False, f"ASPM verification failed: expected 0x{desired_aspm_byte_hex}, got 0x{actual_aspm_byte_hex}"
    
    if verbose:
        print(f"\t[{Colors.GREEN}SUCCESS{Colors.NORMAL}]")
        print(aspm_setting_to_string(ASPM_SETTING))
    
    return True, None


def main():
    """Main function to scan all PCI devices and enable ASPM where needed."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Scan and enable ASPM on PCI devices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  sudo python3 update_aspm.py          # Run with minimal output
  sudo python3 update_aspm.py -v       # Run with verbose output
        """
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output showing detailed ASPM configuration changes"
    )
    args = parser.parse_args()
    
    # Check if running as root
    if os.geteuid() != 0:
        print("This needs to be run as root")
        sys.exit(1)
    
    print(f"{Colors.CYAN}Scanning all PCI devices for ASPM support...{Colors.NORMAL}\n")
    
    # Get all PCI devices
    devices = get_all_pci_devices()
    if not devices:
        print(f"{Colors.RED}No PCI devices found{Colors.NORMAL}")
        sys.exit(1)
    
    print(f"Found {len(devices)} PCI devices\n")
    
    # Track statistics
    devices_with_aspm = 0
    devices_already_set = 0
    devices_updated = 0
    devices_failed = 0
    devices_no_aspm = 0
    
    # Process each device
    for device in devices:
        # Check ASPM status
        status = check_aspm_status(device)
        
        if status is None:
            devices_no_aspm += 1
            continue
        
        aspm_addr, current_val, desired_val = status
        devices_with_aspm += 1
        
        # Get device description
        returncode, device_info = run_command(["lspci", "-s", device])
        
        # Check if already set correctly
        if current_val == desired_val:
            devices_already_set += 1
            if args.verbose:
                print(f"{Colors.GREEN}✓{Colors.NORMAL} {device}: {device_info}")
                print(f"  ASPM already enabled (0x{aspm_addr}: 0x{current_val:X})")
            continue
        
        # Need to update
        if not args.verbose:
            print(f"{Colors.YELLOW}⚠{Colors.NORMAL} {device}: {device_info}")
            print(f"  Current: 0x{aspm_addr}: 0x{current_val:X} → Desired: 0x{desired_val:X}")
            print(f"  Enabling ASPM... ", end="", flush=True)
        
        # Try to enable
        success, error_msg = enable_aspm_byte(device, verbose=args.verbose)
        if success:
            devices_updated += 1
            if not args.verbose:
                print(f"{Colors.GREEN}SUCCESS{Colors.NORMAL}")
        else:
            devices_failed += 1
            if not args.verbose:
                print(f"{Colors.RED}FAILED{Colors.NORMAL}")
            # Always print error messages
            if error_msg:
                print(f"  {Colors.RED}Error: {error_msg}{Colors.NORMAL}")
    
    # Print summary
    print(f"\n{Colors.CYAN}{'='*60}{Colors.NORMAL}")
    print(f"{Colors.CYAN}Summary:{Colors.NORMAL}")
    print(f"  Total PCI devices: {len(devices)}")
    print(f"  Devices with ASPM support: {devices_with_aspm}")
    print(f"  Devices without ASPM support: {devices_no_aspm}")
    print(f"  {Colors.GREEN}Already configured correctly: {devices_already_set}{Colors.NORMAL}")
    print(f"  {Colors.GREEN}Successfully updated: {devices_updated}{Colors.NORMAL}")
    if devices_failed > 0:
        print(f"  {Colors.RED}Failed to update: {devices_failed}{Colors.NORMAL}")
    print(f"{Colors.CYAN}{'='*60}{Colors.NORMAL}")


if __name__ == "__main__":
    main()