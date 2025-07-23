#!/usr/bin/env python3
import subprocess
import time
import tempfile
import os
import uuid
import re
import threading
import signal

class TmuxShellController:
    def __init__(self, session_name="mysession", delay=0.5, max_wait=300):
        self.session = session_name
        self.delay = delay
        self.max_wait = max_wait  # Increased default timeout for long commands
        self.last_output_hash = None

    def send_command(self, command):
        """Send a command to the tmux session."""
        try:
            subprocess.run(['tmux', 'send-keys', '-t', self.session, command, 'Enter'], check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to send command to tmux: {e}")

    def capture_output(self, history_lines=10000):
        """Capture the entire tmux pane content including scrollback."""
        try:
            # Capture with history to get more content
            result = subprocess.run(
                ['tmux', 'capture-pane', '-t', self.session, '-p', '-S', f'-{history_lines}'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to capture tmux output: {e}")

    def get_cursor_position(self):
        """Get cursor position to detect if command is still running."""
        try:
            result = subprocess.run(
                ['tmux', 'display-message', '-t', self.session, '-p', '#{cursor_x},#{cursor_y}'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def wait_for_command_completion(self, timeout=None, check_interval=0.5):
        """
        Advanced method to wait for command completion using multiple indicators.
        """
        if timeout is None:
            timeout = self.max_wait
        
        start_time = time.time()
        last_output_hash = None
        last_cursor_pos = None
        stable_count = 0
        min_stable_time = 1.5  # Reduced for faster detection
        
        print(f"Waiting for command completion (timeout: {timeout}s)...")
        
        while time.time() - start_time < timeout:
            # Use hash for large outputs to detect changes more efficiently
            current_output = self.capture_output(1000)  # Smaller buffer for speed
            current_output_hash = hash(current_output)
            current_cursor = self.get_cursor_position()
            
            # Check if output and cursor position are stable
            if (current_output_hash == last_output_hash and 
                current_cursor == last_cursor_pos and 
                current_cursor is not None):
                stable_count += 1
                
                # If stable for enough cycles, check for prompt
                if stable_count >= (min_stable_time / check_interval):
                    if self._has_prompt_at_end(current_output):
                        print("Command completed (prompt detected)")
                        return True
            else:
                stable_count = 0
            
            last_output_hash = current_output_hash
            last_cursor_pos = current_cursor
            
            # Show progress indicator for long commands
            elapsed = time.time() - start_time
            if elapsed > 5 and int(elapsed) % 10 == 0:
                print(f"Still waiting... ({int(elapsed)}s elapsed)")
            
            time.sleep(check_interval)
        
        print(f"Command timed out after {timeout}s")
        return False

    def _has_prompt_at_end(self, output):
        """Check if the output ends with a shell prompt."""
        if not output.strip():
            return False
            
        lines = output.strip().split('\n')
        if not lines:
            return False
            
        last_line = lines[-1].strip()
        
        # Enhanced prompt patterns
        prompt_patterns = [
            r'.*[$#]\s*$',                    # Basic $ or # prompts
            r'.*>\s*$',                       # > prompts
            r'.*@.*:.*[$#]\s*$',             # user@host:path$ format
            r'.*@.*:.*>\s*$',                # user@host:path> format
            r'^\S+:\S*[$#]\s*$',             # Simple host:path$ format
            r'.*\$\s*$',                     # Ends with $ (catch-all)
            r'.*#\s*$',                      # Ends with # (catch-all)
        ]
        
        for pattern in prompt_patterns:
            if re.match(pattern, last_line):
                return True
        
        # Additional check: if the line is short and contains typical prompt chars
        if len(last_line) < 100 and any(char in last_line for char in ['$', '#', '>', ':']):
            # And doesn't look like command output
            if not any(keyword in last_line.lower() for keyword in 
                      ['error', 'warning', 'failed', 'success', 'completed', 'finished']):
                return True
                
        return False

    def run_with_unique_markers(self, command):
        """Run command using unique markers - improved version for large outputs."""
        # Create unique markers
        start_marker = f"CMDSTART{uuid.uuid4().hex[:8]}"
        end_marker = f"CMDEND{uuid.uuid4().hex[:8]}"
        
        try:
            # Send start marker
            self.send_command(f"echo '{start_marker}'")
            time.sleep(0.5)
            
            # Send the actual command
            print(f"Executing: {command}")
            self.send_command(command)
            
            # Wait for command completion
            if not self.wait_for_command_completion():
                print("Warning: Command may not have completed fully")
            
            # Send end marker
            self.send_command(f"echo '{end_marker}'")
            time.sleep(0.8)
            
            # Capture final output with large buffer
            final_output = self.capture_output(50000)
            
            # Extract content between markers
            result = self._extract_between_markers(final_output, start_marker, end_marker, command)
            
            return result
            
        except Exception as e:
            print(f"Error in marker-based execution: {e}")
            return self.run_simple_fallback(command)

    def _extract_between_markers(self, output, start_marker, end_marker, original_command):
        """Extract output between unique markers."""
        lines = output.splitlines()
        start_idx = -1
        end_idx = -1
        
        # Find marker positions
        for i, line in enumerate(lines):
            if start_marker in line:
                start_idx = i
            elif end_marker in line and start_idx != -1:
                end_idx = i
                break
        
        if start_idx == -1 or end_idx == -1:
            print("Warning: Markers not found, using fallback method")
            return self.run_simple_fallback(original_command)
        
        # Extract lines between markers
        extracted_lines = []
        for i in range(start_idx + 1, end_idx):
            line = lines[i]
            # Skip command echo lines
            if not self._is_command_echo(line, original_command):
                extracted_lines.append(line)
        
        return '\n'.join(extracted_lines).strip()

    def _is_command_echo(self, line, command):
        """Check if line is echoing the command."""
        stripped = line.strip()
        if not stripped:
            return False
        
        # Remove prompt parts and see if what remains matches the command
        # This is a simplified check - you might need to adjust based on your shell
        for prompt_char in ['$', '#', '>']:
            if prompt_char in stripped:
                after_prompt = stripped.split(prompt_char, 1)[-1].strip()
                if after_prompt == command:
                    return True
        
        return stripped == command

    def run_simple_fallback(self, command):
        """Improved fallback method for large outputs."""
        print("Using fallback method...")
        
        try:
            # Temporarily increase history limit
            subprocess.run(['tmux', 'set-option', '-t', self.session, 'history-limit', '50000'], 
                         capture_output=True)
            
            # Clear screen and send a unique start marker
            clear_marker = f"__CLEAR_{uuid.uuid4().hex[:8]}__"
            self.send_command('clear')
            time.sleep(0.3)
            self.send_command(f'echo "{clear_marker}"')
            time.sleep(0.3)
            
            # Send command
            print(f"Fallback executing: {command}")
            self.send_command(command)
            
            # Wait for completion
            self.wait_for_command_completion()
            
            # Send end marker
            end_marker = f"__END_{uuid.uuid4().hex[:8]}__"
            self.send_command(f'echo "{end_marker}"')
            time.sleep(0.5)
            
            # Capture and parse output with large buffer
            output = self.capture_output(50000)
            
            # Find content between our markers
            lines = output.splitlines()
            start_idx = -1
            end_idx = -1
            
            # Find the markers
            for i, line in enumerate(lines):
                if clear_marker in line:
                    start_idx = i
                elif end_marker in line and start_idx != -1:
                    end_idx = i
                    break
            
            if start_idx != -1 and end_idx != -1:
                # Extract lines between markers, skipping command echo
                result_lines = []
                command_found = False
                
                for i in range(start_idx + 1, end_idx):
                    line = lines[i]
                    
                    # Skip the command echo line
                    if not command_found and command in line:
                        # This might be the command echo, skip it
                        if any(prompt in line for prompt in ['$', '#', '>', '└─']):
                            command_found = True
                            continue
                    
                    # Skip empty lines at the beginning
                    if not result_lines and not line.strip():
                        continue
                        
                    result_lines.append(line)
                
                result = '\n'.join(result_lines).strip()
            else:
                print("Warning: Could not find markers in output")
                # Last resort: try to get recent output
                result = self._extract_recent_output(output, command)
            
            # Reset history limit
            subprocess.run(['tmux', 'set-option', '-t', self.session, 'history-limit', '10000'], 
                         capture_output=True)
            
            return result
            
        except Exception as e:
            print(f"Fallback method error: {e}")
            # Reset history limit on error
            subprocess.run(['tmux', 'set-option', '-t', self.session, 'history-limit', '10000'], 
                         capture_output=True)
            return f"Error executing command: {e}"

    def _extract_recent_output(self, output, command):
        """Extract recent command output as last resort."""
        lines = output.splitlines()
        
        # Look for the command from the end
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i]
            if command in line and any(prompt in line for prompt in ['$', '#', '>', '└─']):
                # Found command, extract everything after it until next prompt
                result_lines = []
                for j in range(i + 1, len(lines)):
                    next_line = lines[j]
                    # Stop at next prompt
                    if any(prompt in next_line for prompt in ['└─', '$', '#', '>']):
                        if any(char in next_line for char in ['@', ':', '~']) or 'kali' in next_line:
                            break
                    result_lines.append(next_line)
                
                return '\n'.join(result_lines).strip()
        
        # If we can't find the command, return last few lines
        return '\n'.join(lines[-50:]).strip() if lines else ""

    def run(self, command):
        """Main method to run commands - tries markers first, falls back if needed."""
        if not command.strip():
            return ""
        
        try:
            return self.run_with_unique_markers(command)
        except Exception as e:
            print(f"Primary method failed: {e}")
            return self.run_simple_fallback(command)

    def run_with_timeout(self, command, timeout=60):
        """Run command with explicit timeout."""
        old_max_wait = self.max_wait
        self.max_wait = timeout
        try:
            return self.run(command)
        finally:
            self.max_wait = old_max_wait

    def interrupt_command(self):
        """Send Ctrl+C to interrupt current command."""
        try:
            subprocess.run(['tmux', 'send-keys', '-t', self.session, 'C-c'], check=True)
            time.sleep(1)
            return True
        except subprocess.CalledProcessError:
            return False

    def check_session(self):
        """Check if tmux session exists."""
        try:
            subprocess.run(['tmux', 'has-session', '-t', self.session], 
                         check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def connect_to_session(self):
        """Connect to existing tmux session or exit if it doesn't exist."""
        if not self.check_session():
            raise RuntimeError(f"Tmux session '{self.session}' does not exist. Please create it first or use an existing session name.")
        else:
            print(f"Connected to existing tmux session: {self.session}")

    def get_session_info(self):
        """Get information about the current session."""
        try:
            result = subprocess.run(
                ['tmux', 'display-message', '-t', self.session, '-p', 
                 'Session: #{session_name}, Window: #{window_name}, Pane: #{pane_index}'],
                stdout=subprocess.PIPE,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return "Session info unavailable"


def signal_handler(signum, frame):
    print("\nReceived interrupt signal. Use 'exit' to quit properly or 'interrupt' to stop current command.")


if __name__ == "__main__":
    # Set up signal handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    
    shell = TmuxShellController(session_name="shrem", delay=0.5, max_wait=300)
    
    # Connect to existing session (don't create if it doesn't exist)
    try:
        shell.connect_to_session()
    except RuntimeError as e:
        print(f"Error: {e}")
        print("Available tmux sessions:")
        try:
            result = subprocess.run(['tmux', 'list-sessions'], capture_output=True, text=True)
            if result.returncode == 0:
                print(result.stdout)
            else:
                print("No tmux sessions found.")
        except subprocess.CalledProcessError:
            print("No tmux sessions found or tmux not available.")
        exit(1)
    
    print("=== Enhanced Tmux Shell Controller ===")
    print(f"Connected to tmux session '{shell.session}'")
    print(shell.get_session_info())
    print("\nCommands:")
    print("  exit/quit     - Exit the controller")
    print("  interrupt     - Send Ctrl+C to current command")
    print("  timeout <sec> <cmd> - Run command with specific timeout")
    print("  info          - Show session information")
    print("\nThis version handles both short and long-running commands automatically.")
    print("Long commands will show progress indicators.\n")
    
    while True:
        try:
            cmd = input("Command > ").strip()
            
            if cmd.lower() in {"exit", "quit"}:
                break
            elif cmd.lower() == "interrupt":
                if shell.interrupt_command():
                    print("Sent interrupt signal (Ctrl+C)")
                else:
                    print("Failed to send interrupt signal")
                continue
            elif cmd.lower() == "info":
                print(shell.get_session_info())
                continue
            elif not cmd:
                continue
            
            # Handle timeout command
            if cmd.startswith('timeout '):
                parts = cmd.split(' ', 2)
                if len(parts) >= 3:
                    try:
                        timeout_val = int(parts[1])
                        actual_cmd = parts[2]
                        print(f"Running with {timeout_val}s timeout: {actual_cmd}")
                        output = shell.run_with_timeout(actual_cmd, timeout_val)
                    except ValueError:
                        print("Usage: timeout <seconds> <command>")
                        continue
                else:
                    print("Usage: timeout <seconds> <command>")
                    continue
            else:
                start_time = time.time()
                output = shell.run(cmd)
                elapsed = time.time() - start_time
                
                print(f"=== Output (completed in {elapsed:.1f}s) ===")
            
            if output:
                print(output)
            else:
                print("(No output or command completed silently)")
            print()
            
        except KeyboardInterrupt:
            print("\nUse 'exit' to quit or 'interrupt' to stop current command.")
        except Exception as e:
            print(f"[Error] {e}")
            print("Continuing...")
