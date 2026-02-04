#!/usr/bin/env python3
"""
VortexL2 - L2TPv3 Tunnel Manager

Main entry point and CLI handler.
"""

import sys
import os
import argparse
import subprocess
import signal

# Ensure we can import the package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vortexl2 import __version__
from vortexl2.config import TunnelConfig, ConfigManager
from vortexl2.tunnel import TunnelManager
from vortexl2.forward import ForwardManager
from vortexl2 import ui


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    print("\n")
    ui.console.print("[yellow]Interrupted. Goodbye![/]")
    sys.exit(0)


def check_root():
    """Check if running as root."""
    if os.geteuid() != 0:
        ui.show_error("VortexL2 must be run as root (use sudo)")
        sys.exit(1)


def cmd_apply():
    """
    Apply all tunnel configurations (idempotent).
    Used by systemd service on boot.
    """
    manager = ConfigManager()
    tunnels = manager.get_all_tunnels()
    
    if not tunnels:
        print("VortexL2: No tunnels configured, skipping")
        return 0
    
    errors = 0
    for config in tunnels:
        if not config.is_configured():
            print(f"VortexL2: Tunnel '{config.name}' not fully configured, skipping")
            continue
        
        tunnel = TunnelManager(config)
        forward = ForwardManager(config)
        
        # Setup tunnel
        success, msg = tunnel.full_setup()
        print(f"Tunnel '{config.name}': {msg}")
        
        if not success:
            errors += 1
            continue
        
        # Setup forwards if configured
        if config.forwarded_ports:
            success, msg = forward.install_template()
            print(f"Forward template: {msg}")
            
            success, msg = forward.start_all_forwards()
            print(f"Port forwards: {msg}")
    
    return 1 if errors > 0 else 0


def handle_prerequisites(current_tunnel: TunnelConfig):
    """Handle prerequisites installation."""
    ui.show_banner(current_tunnel)
    ui.show_info("Installing prerequisites...")
    
    # Use any tunnel config for prerequisites (they're system-wide)
    tunnel = TunnelManager(current_tunnel) if current_tunnel else TunnelManager(TunnelConfig("temp"))
    
    success, msg = tunnel.install_prerequisites()
    ui.show_output(msg, "Prerequisites Installation")
    
    if success:
        ui.show_success("Prerequisites installed successfully")
    else:
        ui.show_error(msg)
    
    ui.wait_for_enter()


def handle_tunnel_management(manager: ConfigManager, current_tunnel: TunnelConfig) -> TunnelConfig:
    """Handle tunnel management submenu. Returns updated current tunnel."""
    while True:
        ui.show_banner(current_tunnel)
        ui.show_tunnel_list(manager, current_tunnel.name if current_tunnel else None)
        ui.console.print()
        
        choice = ui.show_tunnel_menu()
        
        if choice == "0":
            break
        elif choice == "1":
            # List tunnels (already shown above)
            ui.wait_for_enter()
        elif choice == "2":
            # Add new tunnel
            name = ui.prompt_tunnel_name()
            if name:
                if manager.tunnel_exists(name):
                    ui.show_error(f"Tunnel '{name}' already exists")
                else:
                    new_tunnel = manager.create_tunnel(name)
                    ui.show_success(f"Tunnel '{name}' created with interface {new_tunnel.interface_name}")
                    current_tunnel = new_tunnel
            ui.wait_for_enter()
        elif choice == "3":
            # Select tunnel
            selected = ui.prompt_select_tunnel(manager)
            if selected:
                current_tunnel = manager.get_tunnel(selected)
                ui.show_success(f"Switched to tunnel '{selected}'")
            ui.wait_for_enter()
        elif choice == "4":
            # Delete tunnel
            ui.show_tunnel_list(manager, current_tunnel.name if current_tunnel else None)
            selected = ui.prompt_select_tunnel(manager)
            if selected:
                if ui.confirm(f"Are you sure you want to delete tunnel '{selected}'?", default=False):
                    # Stop tunnel first if running
                    tunnel_config = manager.get_tunnel(selected)
                    if tunnel_config:
                        tunnel_mgr = TunnelManager(tunnel_config)
                        tunnel_mgr.full_teardown()
                    
                    manager.delete_tunnel(selected)
                    ui.show_success(f"Tunnel '{selected}' deleted")
                    
                    # If deleted current tunnel, switch to another
                    if current_tunnel and current_tunnel.name == selected:
                        tunnels = manager.list_tunnels()
                        if tunnels:
                            current_tunnel = manager.get_tunnel(tunnels[0])
                        else:
                            current_tunnel = None
            ui.wait_for_enter()
    
    return current_tunnel


def handle_configure(current_tunnel: TunnelConfig):
    """Handle tunnel configuration."""
    if not current_tunnel:
        ui.show_error("No tunnel selected. Create or select a tunnel first.")
        ui.wait_for_enter()
        return
    
    ui.show_banner(current_tunnel)
    ui.prompt_tunnel_config(current_tunnel)
    ui.wait_for_enter()


def handle_start_tunnel(current_tunnel: TunnelConfig):
    """Handle tunnel start."""
    if not current_tunnel:
        ui.show_error("No tunnel selected.")
        ui.wait_for_enter()
        return
    
    ui.show_banner(current_tunnel)
    
    if not current_tunnel.is_configured():
        ui.show_error("Please configure tunnel first (option 3)")
        ui.wait_for_enter()
        return
    
    tunnel = TunnelManager(current_tunnel)
    
    # Check if tunnel exists
    if tunnel.check_tunnel_exists():
        ui.show_warning("Tunnel already exists")
        if not ui.confirm("Delete existing tunnel and recreate?", default=False):
            ui.wait_for_enter()
            return
        
        success, msg = tunnel.full_teardown()
        ui.show_output(msg, "Teardown")
    
    ui.show_info("Creating tunnel...")
    success, msg = tunnel.full_setup()
    ui.show_output(msg, "Tunnel Setup")
    
    if success:
        ui.show_success("Tunnel started successfully")
    else:
        ui.show_error("Tunnel creation failed")
    
    ui.wait_for_enter()


def handle_stop_tunnel(current_tunnel: TunnelConfig):
    """Handle tunnel stop."""
    if not current_tunnel:
        ui.show_error("No tunnel selected.")
        ui.wait_for_enter()
        return
    
    ui.show_banner(current_tunnel)
    
    if not ui.confirm(f"Are you sure you want to stop tunnel '{current_tunnel.name}'?", default=False):
        return
    
    tunnel = TunnelManager(current_tunnel)
    forward = ForwardManager(current_tunnel)
    
    # Stop forwards first
    if current_tunnel.forwarded_ports:
        ui.show_info("Stopping port forwards...")
        success, msg = forward.stop_all_forwards()
        ui.show_output(msg, "Stop Forwards")
    
    ui.show_info("Stopping tunnel...")
    success, msg = tunnel.full_teardown()
    ui.show_output(msg, "Tunnel Teardown")
    
    if success:
        ui.show_success("Tunnel stopped successfully")
    else:
        ui.show_error("Tunnel stop failed")
    
    ui.wait_for_enter()


def handle_forwards_menu(current_tunnel: TunnelConfig):
    """Handle port forwards submenu."""
    if not current_tunnel:
        ui.show_error("No tunnel selected.")
        ui.wait_for_enter()
        return
    
    forward = ForwardManager(current_tunnel)
    
    while True:
        ui.show_banner(current_tunnel)
        
        # Show current forwards
        forwards = forward.list_forwards()
        if forwards:
            ui.show_forwards_list(forwards)
        
        choice = ui.show_forwards_menu()
        
        if choice == "0":
            break
        elif choice == "1":
            # Add forwards
            ports = ui.prompt_ports()
            if ports:
                success, msg = forward.add_multiple_forwards(ports)
                ui.show_output(msg, "Add Forwards")
            ui.wait_for_enter()
        elif choice == "2":
            # Remove forwards
            ports = ui.prompt_ports()
            if ports:
                success, msg = forward.remove_multiple_forwards(ports)
                ui.show_output(msg, "Remove Forwards")
            ui.wait_for_enter()
        elif choice == "3":
            # List forwards (already shown above)
            ui.wait_for_enter()
        elif choice == "4":
            # Restart all
            success, msg = forward.restart_all_forwards()
            ui.show_output(msg, "Restart Forwards")
            ui.wait_for_enter()
        elif choice == "5":
            # Stop all
            success, msg = forward.stop_all_forwards()
            ui.show_output(msg, "Stop Forwards")
            ui.wait_for_enter()
        elif choice == "6":
            # Start all
            success, msg = forward.start_all_forwards()
            ui.show_output(msg, "Start Forwards")
            ui.wait_for_enter()


def handle_status(current_tunnel: TunnelConfig):
    """Handle status display."""
    if not current_tunnel:
        ui.show_error("No tunnel selected.")
        ui.wait_for_enter()
        return
    
    ui.show_banner(current_tunnel)
    
    tunnel = TunnelManager(current_tunnel)
    forward = ForwardManager(current_tunnel)
    
    # Tunnel status
    status = tunnel.get_status()
    ui.show_status(status)
    
    # Forward status
    if current_tunnel.forwarded_ports:
        ui.console.print()
        forwards = forward.list_forwards()
        ui.show_forwards_list(forwards)
    
    ui.wait_for_enter()


def handle_logs(current_tunnel: TunnelConfig):
    """Handle log viewing."""
    ui.show_banner(current_tunnel)
    
    services = ["vortexl2-tunnel"]
    
    # Add forward services for current tunnel
    if current_tunnel and current_tunnel.forwarded_ports:
        for port in current_tunnel.forwarded_ports:
            services.append(f"vortexl2-forward@{port}")
    
    for service in services:
        result = subprocess.run(
            f"journalctl -u {service} -n 20 --no-pager",
            shell=True,
            capture_output=True,
            text=True
        )
        output = result.stdout or result.stderr or "No logs available"
        ui.show_output(output, f"Logs: {service}")
    
    ui.wait_for_enter()


def main_menu():
    """Main interactive menu loop."""
    check_root()
    
    # Set up signal handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    
    # Clear screen before starting
    ui.clear_screen()
    
    # Initialize config manager and get/create first tunnel
    manager = ConfigManager()
    tunnels = manager.list_tunnels()
    
    if tunnels:
        current_tunnel = manager.get_tunnel(tunnels[0])
    else:
        # Create default tunnel
        current_tunnel = manager.create_tunnel("tunnel1")
        ui.show_info("Created default tunnel 'tunnel1'")
    
    while True:
        ui.show_banner(current_tunnel)
        choice = ui.show_main_menu()
        
        try:
            if choice == "0":
                ui.console.print("\n[bold green]Goodbye![/]\n")
                break
            elif choice == "1":
                handle_prerequisites(current_tunnel)
            elif choice == "2":
                current_tunnel = handle_tunnel_management(manager, current_tunnel)
                # Refresh tunnel list after management
                if not current_tunnel:
                    tunnels = manager.list_tunnels()
                    if tunnels:
                        current_tunnel = manager.get_tunnel(tunnels[0])
            elif choice == "3":
                handle_configure(current_tunnel)
            elif choice == "4":
                handle_start_tunnel(current_tunnel)
            elif choice == "5":
                handle_stop_tunnel(current_tunnel)
            elif choice == "6":
                handle_forwards_menu(current_tunnel)
            elif choice == "7":
                handle_status(current_tunnel)
            elif choice == "8":
                handle_logs(current_tunnel)
            else:
                ui.show_warning("Invalid option")
                ui.wait_for_enter()
        except KeyboardInterrupt:
            ui.console.print("\n[yellow]Interrupted[/]")
            continue
        except Exception as e:
            ui.show_error(f"Error: {e}")
            ui.wait_for_enter()


def main():
    """CLI entry point."""
    # Set up signal handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    
    parser = argparse.ArgumentParser(
        description="VortexL2 - L2TPv3 Tunnel Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  (none)     Open interactive management panel
  apply      Apply all tunnel configurations (used by systemd)

Examples:
  sudo vortexl2           # Open management panel
  sudo vortexl2 apply     # Apply all tunnels (for systemd)
        """
    )
    parser.add_argument(
        'command',
        nargs='?',
        choices=['apply'],
        help='Command to run'
    )
    parser.add_argument(
        '--version', '-v',
        action='version',
        version=f'VortexL2 {__version__}'
    )
    
    args = parser.parse_args()
    
    if args.command == 'apply':
        check_root()
        sys.exit(cmd_apply())
    else:
        main_menu()


if __name__ == "__main__":
    main()
