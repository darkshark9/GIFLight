import asyncio
import concurrent.futures
import glob
import os
import shutil
import sys
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk, messagebox
from typing import Optional, Tuple, List
import platform
import ttkbootstrap as ttk
from PIL import Image, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD

from options_window import OptionsWindow

if sys.platform == 'win32':
    import subprocess

    CREATE_NO_WINDOW = 0x08000000
    SW_HIDE = 0x0
else:
    CREATE_NO_WINDOW = 0
    SW_HIDE = 0

# Determine the base path when running in a frozen bundle (PyInstaller)
os.environ["MAGICK_DEBUG"] = "Module"
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    base_path = os.path.abspath(".")

# Construct the path to the coders directory that we bundled
coder_path = os.path.join(base_path, "modules-Q16HDRI", "coders")

# Set the MAGICK_CODER_MODULE_PATH environment variable so ImageMagick can find its delegate modules
os.environ["MAGICK_CODER_MODULE_PATH"] = coder_path


# (Optional) You can print or log the path for debugging:
# print("MAGICK_CODER_MODULE_PATH set to:", coder_path)

def get_binary_path(binary_name: str) -> str:
    """
    Get the correct path to a bundled binary, handling PyInstaller directories
    and macOS ARM64 vs Intel differences.
    """
    # Determine base directory for binaries
    if getattr(sys, 'frozen', False):
        base_path = os.path.join(sys._MEIPASS, 'bin')
    else:
        # In development, binaries are in a local "bin" subfolder
        base_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'bin')

    # Only allow known binaries
    allowed_binaries = {'ffmpeg', 'gifski', 'gifsicle', 'ffprobe', 'magick'}
    binary_base = os.path.splitext(binary_name.lower())[0]
    if binary_base not in allowed_binaries:
        raise ValueError(f"Invalid binary name: {binary_name}")

    # Append extension for Windows executables
    if sys.platform == 'win32':
        binary_filename = f"{binary_base}.exe"
    else:
        # On macOS (and other Unix), use the base name (executables typically have no extension)
        binary_filename = binary_base

    # Handle macOS ARM64: prefer arm64-specific binaries if available
    if sys.platform == 'darwin' and platform.machine() == 'arm64':
        arm64_dir = os.path.join(base_path, 'arm64')
        if os.path.isdir(arm64_dir):
            base_path = arm64_dir  # Use dedicated arm64 subdirectory if present
        # If no arm64 subdirectory, consider arm64 or universal binaries in main bin
        arm64_variant = os.path.join(base_path, f"{binary_base}_arm64")
        universal_variant = os.path.join(base_path, f"{binary_base}_universal")
        if os.path.exists(arm64_variant):
            binary_path = arm64_variant
        elif os.path.exists(universal_variant):
            binary_path = universal_variant
        else:
            # Fall back to default location in base_path
            binary_path = os.path.join(base_path, binary_filename)
    else:
        # Non-ARM64 or non-macOS: use default path
        binary_path = os.path.join(base_path, binary_filename)

    # Verify the binary file exists
    if not os.path.isfile(binary_path):
        raise FileNotFoundError(f"Required component not found: {os.path.basename(binary_path)}")
    # Ensure the binary resides in the expected directory (security check)
    if not os.path.normpath(binary_path).startswith(os.path.normpath(base_path)):
        raise ValueError(f"Binary path points outside of expected directory: {binary_path}")

    # On macOS, ensure the binary has execute permission
    if sys.platform == 'darwin':
        try:
            os.chmod(binary_path, 0o755)  # rwx for owner, rx for group/others
        except Exception as e:
            print(f"Warning: Could not set executable permissions on {binary_path}: {e}")
    return binary_path


# Update all the paths using the same function
FFMPEG_PATH = get_binary_path('ffmpeg')

GIFSKI_PATH = get_binary_path('gifski')
GIFSICLE_PATH = get_binary_path('gifsicle')
IMAGEMAGICK_PATH = get_binary_path('magick')

# Add to the allowed_binaries set in get_binary_path:
allowed_binaries = {'ffmpeg', 'gifski', 'gifsicle', 'ffprobe', 'magick'}


class BatchProcessingFrame(ttk.Frame):
    """Frame to handle batch processing of multiple files"""

    def __init__(self, master, converter):
        super().__init__(master)
        self.converter = converter
        self.file_queue: List[str] = []
        self.current_processing_index: int = -1
        self.is_processing_batch: bool = False

        self.setup_styles()
        self.create_widgets()

        # Configure the frame to expand properly
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

    def setup_styles(self):
        """Configure styles for batch processing widgets"""
        self.style = ttk.Style()

        # Queue list style
        self.style.configure('Queue.Treeview',
                             background='#2d2d2d',
                             foreground='white',
                             fieldbackground='#2d2d2d',
                             rowheight=22)

        self.style.map('Queue.Treeview',
                       background=[('selected', '#3d3d3d')],
                       foreground=[('selected', 'white')])

        # Batch control buttons
        self.style.configure('BatchControl.TButton',
                             background='#007bff',
                             foreground='white',
                             font=('Segoe UI', 9))

        self.style.configure('BatchRemove.TButton',
                             background='#dc3545',
                             foreground='white',
                             font=('Segoe UI', 9))

        # Status colors
        self.style.configure('Success.TLabel',
                             foreground='#28a745')

        self.style.configure('Error.TLabel',
                             foreground='#dc3545')

        # Header frame style
        self.style.configure('BatchHeader.TFrame',
                             background='#252525')

        # Batch queue frame style
        self.style.configure('BatchQueue.TFrame',
                             background='#1e1e1e')

    def create_widgets(self):
        """Create and arrange widgets for batch processing"""
        # Batch processing header with border and padding
        header_frame = ttk.Frame(self, style='BatchHeader.TFrame')
        header_frame.grid(row=0, column=0, sticky='ew', pady=(0, 2), padx=(0, 0))
        header_frame.columnconfigure(0, weight=1)  # Make label expand
        header_frame.columnconfigure(3, weight=0)  # Button column fixed width

        batch_label = ttk.Label(
            header_frame,
            text="Batch Processing Queue",
            font=('Segoe UI', 10, 'bold')
        )
        batch_label.grid(row=0, column=0, sticky='w', padx=5, pady=5)

        # Buttons for queue management - positioned in header
        self.remove_button = ttk.Button(
            header_frame,
            text="Remove Selected",
            style='BatchRemove.TButton',
            command=self.remove_selected_file
        )
        self.remove_button.grid(row=0, column=2, padx=5, pady=5)

        self.clear_button = ttk.Button(
            header_frame,
            text="Clear All",
            style='BatchRemove.TButton',
            command=self.clear_queue
        )
        self.clear_button.grid(row=0, column=3, padx=5, pady=5)

        # Create queue list with scrollbar in a frame
        queue_frame = ttk.Frame(self, style='BatchQueue.TFrame')
        queue_frame.grid(row=1, column=0, sticky='nsew', padx=5, pady=5)
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(0, weight=1)

        # Treeview for file queue - add 'size' column
        self.queue_list = ttk.Treeview(
            queue_frame,
            columns=('file', 'status', 'size'),
            show='headings',
            style='Queue.Treeview',
            height=8  # Set a fixed height to prevent excessive expansion
        )

        # Configure columns
        self.queue_list.heading('file', text='File')
        self.queue_list.heading('status', text='Status')
        self.queue_list.heading('size', text='Size')

        self.queue_list.column('file', width=250)
        self.queue_list.column('status', width=100, anchor='center')
        self.queue_list.column('size', width=80, anchor='center')

        # Add scrollbar
        scrollbar = ttk.Scrollbar(queue_frame, command=self.queue_list.yview)
        self.queue_list.configure(yscrollcommand=scrollbar.set)

        # Place widgets
        self.queue_list.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')

        # Status bar
        status_frame = ttk.Frame(self)
        status_frame.grid(row=2, column=0, sticky='ew', padx=5, pady=(5, 0))
        status_frame.columnconfigure(0, weight=1)

        self.status_label = ttk.Label(
            status_frame,
            text="Ready",
            font=('Segoe UI', 9)
        )
        self.status_label.grid(row=0, column=0, sticky='w')

    def update_queue_status(self):
        """Update the status display of the queue"""
        queue_size = len(self.file_queue)
        if queue_size == 0:
            status_text = "Queue empty"
        elif queue_size == 1:
            status_text = "1 file in queue"
        else:
            status_text = f"{queue_size} files in queue"

        if self.current_processing_index >= 0:
            status_text += f" | Processing {self.current_processing_index + 1}/{queue_size}"

        self.status_label.configure(text=status_text)

    def add_file_to_queue(self, file_path: str) -> bool:
        """
        Add a file to the processing queue

        Args:
            file_path: Path to the file to add

        Returns:
            bool: True if file was added, False if it was already in the queue
        """
        # Check if file is already in queue
        for existing_file in self.file_queue:
            if existing_file == file_path:
                return False

        # Add file to queue
        self.file_queue.append(file_path)

        # Add to treeview
        filename = os.path.basename(file_path)
        self.queue_list.insert('', 'end', values=(filename, "Queued", ""))

        # Update the status text
        self.update_queue_status()

        # Update the Convert button text based on the number of files
        self.update_convert_button_text()

        # Enable the convert button whenever we have files in the queue
        if hasattr(self.converter, 'convert_button'):
            self.converter.convert_button.configure(state='normal')

        # Show or hide the smart panel based on the number of files
        self.update_smart_panel_visibility()

        return True

    def update_smart_panel_visibility(self):
        """Show or hide the smart panel based on the number of files in the queue"""
        if hasattr(self.converter, 'smart_panel'):
            if len(self.file_queue) >= 2:
                # Show the smart panel and switch to batch tab
                self.converter.smart_panel.grid()
                if hasattr(self.converter, 'switch_tab'):
                    self.converter.switch_tab("batch")
            else:
                # Hide the smart panel if fewer than 2 files
                self.converter.smart_panel.grid_remove()
                # Reset active tab
                if hasattr(self.converter, 'active_tab'):
                    self.converter.active_tab = None

    def remove_selected_file(self):
        """Remove the selected file from the queue and reset state if queue becomes empty"""
        selected = self.queue_list.selection()
        if not selected:
            return

        for item_id in selected:
            item_values = self.queue_list.item(item_id, 'values')
            filename = item_values[0]

            # Find the corresponding file path
            for file_path in self.file_queue[:]:
                if os.path.basename(file_path) == filename:
                    self.file_queue.remove(file_path)

            # Remove from treeview
            self.queue_list.delete(item_id)

        # Update the status text
        self.update_queue_status()

        # Update the Convert button text based on the number of files
        self.update_convert_button_text()

        # If the queue is now empty, reset the processing state and clear the loaded file
        if not self.file_queue:
            self.current_processing_index = -1
            self.is_processing_batch = False
            if hasattr(self.converter, 'convert_button'):
                # Always set to Convert when queue is empty
                self.converter.convert_button.configure(text="Convert")

            # Also clear the currently loaded file in the converter
            if hasattr(self.converter, 'selected_file'):
                self.converter.selected_file = None
                self.converter.file_label.configure(text="No file selected")
                self.converter.convert_button.configure(state='disabled')

            # Revert the UI to its launch state
            self.reset_ui_to_launch_state()
        else:
            # Update smart panel visibility based on the number of files left
            self.update_smart_panel_visibility()

    def clear_queue(self):
        """Clear all files from the queue and reset the selected file"""
        # Clear the file queue
        self.file_queue.clear()

        # Clear the display
        for item in self.queue_list.get_children():
            self.queue_list.delete(item)

        # Reset processing state
        self.current_processing_index = -1
        self.is_processing_batch = False
        if hasattr(self.converter, 'convert_button'):
            # Set button text based on number of files in queue
            button_text = "Process Batch" if len(self.file_queue) >= 2 else "Convert"
            self.converter.convert_button.configure(text=button_text)

        # Update the status text
        self.update_queue_status()

        # Update the Convert button text based on the number of files
        self.update_convert_button_text()

        # Also clear the currently loaded file in the converter
        if hasattr(self.converter, 'selected_file'):
            self.converter.selected_file = None
            self.converter.file_label.configure(text="No file selected")
            self.converter.convert_button.configure(state='disabled')

        # Revert the UI to its launch state
        self.reset_ui_to_launch_state()

    def update_file_status(self, file_path: str, status: str, size: str = ''):
        """Update the status and size of a specific file in the queue"""
        for item_id in self.queue_list.get_children():
            if self.queue_list.item(item_id, 'values')[0] == os.path.basename(file_path):
                self.queue_list.item(item_id, values=(os.path.basename(file_path), status, size))
                break

    def toggle_batch_processing(self):
        """Toggle between starting and stopping batch processing"""
        if not self.is_processing_batch:
            # Start batch processing
            if not self.file_queue:
                messagebox.showinfo("Batch Processing", "No files in queue")
                return

            if self.converter.is_converting:
                messagebox.showwarning("Batch Processing", "Another conversion is already in progress")
                return

            self.start_batch_processing()
        else:
            # Stop batch processing
            self.stop_batch_processing()

    def start_batch_processing(self):
        """Start processing the file queue"""
        self.is_processing_batch = True
        self.current_processing_index = 0

        # Update button
        if hasattr(self.converter, 'convert_button'):
            self.converter.convert_button.configure(
                text="Stop",
                style='Danger.TButton'
            )

        # Disable queue management during processing
        self.remove_button.configure(state='disabled')
        self.clear_button.configure(state='disabled')

        # Ensure the smart panel and tabs are visible for batch processing
        if hasattr(self.converter, 'smart_panel') and not self.converter.smart_panel.winfo_viewable():
            self.converter.smart_panel.grid()

        # Make sure tab buttons are visible
        if hasattr(self.converter, 'tab_buttons_frame') and not self.converter.tab_buttons_frame.winfo_viewable():
            self.converter.tab_buttons_frame.grid()

        # Switch to batch tab
        if hasattr(self.converter, 'switch_tab'):
            self.converter.switch_tab("batch")

        # Start processing the first file
        self.process_next_file()

    def stop_batch_processing(self):
        """Stop the batch processing"""
        self.is_processing_batch = False

        # Stop the current conversion if active
        if self.converter.is_converting:
            self.converter.stop_conversion()

        # Reset button and enable queue management
        if hasattr(self.converter, 'convert_button'):
            # Set button text based on number of files in queue
            button_text = "Process Batch" if len(self.file_queue) >= 2 else "Convert"
            self.converter.convert_button.configure(
                text=button_text,
                style='Primary.TButton'
            )

        self.remove_button.configure(state='normal')
        self.clear_button.configure(state='normal')

        # Update status of remaining files
        for i in range(self.current_processing_index, len(self.file_queue)):
            self.update_file_status(self.file_queue[i], "Queued")

    def process_next_file(self):
        """Process the next file in the queue"""
        if not self.is_processing_batch or self.current_processing_index >= len(self.file_queue):
            # We're done with the batch
            self.converter.log("Batch processing complete")
            self.stop_batch_processing()
            # Only show message if batch was completed normally (not interrupted)
            if not self.converter.cancellation_event.is_set():
                messagebox.showinfo("Batch Processing", "Batch processing completed")
            return

        # Get the current file
        current_file = self.file_queue[self.current_processing_index]
        self.update_file_status(current_file, "Processing")

        # Log the current file being processed
        self.converter.log(
            f"\n--- Processing file {self.current_processing_index + 1}/{len(self.file_queue)}: {os.path.basename(current_file)} ---\n")

        # Set the file to convert WITH bypass_batch=True to prevent adding to batch again
        self.converter.set_file(current_file, bypass_batch=True)
        self.converter.log(f"Set current file: {current_file}")

        # Start conversion with callback for when it's done
        self.converter.start_batch_conversion(self.on_file_processed)

    def on_file_processed(self, success: bool, file_path: str = None, file_size: int = 0):
        """Callback when a file is processed"""
        try:
            self.converter.log(f"\nBatch processing callback received: success={success}, file_size={file_size}")

            # Update status of the processed file
            if self.current_processing_index < len(self.file_queue):
                current_file = self.file_queue[self.current_processing_index]

                if success:
                    status = "Completed"
                    # Use the file_path parameter if provided, otherwise use current_file
                    actual_path = file_path if file_path else current_file
                    # Get file size if not provided
                    if file_size == 0 and os.path.exists(actual_path):
                        # Check for the optimized version first
                        optimized_path = os.path.splitext(actual_path)[0] + '_optimized.gif'
                        if os.path.exists(optimized_path):
                            file_size = os.path.getsize(optimized_path)
                        else:
                            file_size = os.path.getsize(actual_path)

                    size_text = f"{file_size / 1024:.1f} KB" if file_size > 0 else ""
                    self.converter.log(f"File processed successfully: {size_text}")
                else:
                    status = "Failed"
                    size_text = ""
                    self.converter.log("File processing failed")

                self.update_file_status(current_file, status, size_text)

            # Move to the next file
            self.current_processing_index += 1
            self.converter.log(f"Moving to next file. Current index: {self.current_processing_index}")
            
            # Make sure the button stays in "Stop" state during batch processing
            if hasattr(self.converter, 'convert_button') and self.is_processing_batch:
                self.converter.convert_button.configure(
                    text="Stop",
                    style='Danger.TButton'
                )

            # Short delay before processing the next file
            self.after(1000, self.process_next_file)

        except Exception as e:
            self.converter.log(f"Error in on_file_processed: {str(e)}")
            # Still try to move to the next file
            self.current_processing_index += 1
            self.after(1000, self.process_next_file)

    def update_convert_button_text(self):
        """Update the Convert button text based on the number of files in the queue"""
        if self.converter.batch_frame.winfo_viewable():
            if hasattr(self.converter, 'convert_button'):
                # Set button text based on number of files in queue and processing state
                if self.is_processing_batch:
                    self.converter.convert_button.configure(text="Stop")
                else:
                    button_text = "Process Batch" if len(self.file_queue) >= 2 else "Convert"
                    self.converter.convert_button.configure(text=button_text)

    def reset_ui_to_launch_state(self):
        """Reset the UI to its launch state, hiding tabs, log window, and batch window"""
        if hasattr(self.converter, 'smart_panel'):
            # Hide the smart panel
            self.converter.smart_panel.grid_remove()

            # Reset active tab
            if hasattr(self.converter, 'active_tab'):
                self.converter.active_tab = None

            # If the batch frame is visible, hide it
            if hasattr(self.converter, 'batch_frame') and self.converter.batch_frame.winfo_viewable():
                self.converter.batch_frame.grid_remove()

            # If the log frame is visible, hide it
            if hasattr(self.converter, 'log_frame') and self.converter.log_frame.winfo_viewable():
                self.converter.log_frame.grid_remove()

            # Reset window size to initial dimensions
            self.converter.update_idletasks()
            root = self.converter.winfo_toplevel()
            root.geometry("800x600")


@dataclass(frozen=True)
class OptimizationParams:
    quality: int
    lossy: int
    frame_skip: int
    output_path: str


def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


class DragDropLabel(ttk.Label):
    """Custom label widget that supports drag and drop"""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)

        # Register drop target and bind events
        self.drop_target_register(DND_FILES)

        # Bind the drop event directly to this widget
        self.dnd_bind('<<Drop>>', self.handle_drop)
        self.bind('<Enter>', self.handle_drag_enter)
        self.bind('<Leave>', self.handle_drag_leave)

        # Set initial style
        self.configure(style='Custom.TLabel')

    def handle_drop(self, event):
        """Handle file drop event"""
        try:
            # Get drop data which may contain multiple files
            files_data = event.data

            # Handle multiple files separated by spaces (tkinterdnd2 format)
            # This splits by space but respects quoted paths with spaces
            import re
            file_paths = []

            # Extract paths from the string - they may be quoted or curly-braced
            for match in re.finditer(r'(?:{([^}]+)})|(?:"([^"]+)")|(?:(\S+))', files_data):
                # Get the matched group (only one will be non-None)
                path = next(filter(None, match.groups()))
                file_paths.append(path)

            if not file_paths:
                print("No valid files found in drop data")
                return

            # Process all dropped files
            valid_files = []
            for file_path in file_paths:
                file_path = file_path.strip()

                if self.validate_file(file_path):
                    print(f"Valid file dropped: {file_path}")
                    valid_files.append(file_path)
                else:
                    print(f"Invalid file type: {file_path}")

            # Find converter and process files
            converter = self.find_converter(self)
            if converter:
                if len(valid_files) > 1:
                    # Multiple files - add them all to batch queue
                    print(f"Processing {len(valid_files)} files in batch mode")

                    # Ensure the batch processing UI exists
                    if not hasattr(converter, 'batch_frame') or not converter.batch_frame:
                        converter.create_batch_processing_ui()

                    # Add files one by one
                    for file_path in valid_files:
                        converter.add_to_batch(file_path)

                    # Update smart panel visibility after adding files
                    if hasattr(converter, 'batch_frame') and converter.batch_frame:
                        converter.batch_frame.update_smart_panel_visibility()

                    # Ensure the convert button is enabled
                    if hasattr(converter, 'convert_button'):
                        converter.convert_button.configure(state='normal')

                elif len(valid_files) == 1:
                    # Single file - use regular set_file
                    converter.set_file(valid_files[0])

                    # Ensure the convert button is enabled
                    if hasattr(converter, 'convert_button'):
                        converter.convert_button.configure(state='normal')
            else:
                print("Could not find ModernGifConverter instance")

        except Exception as e:
            print(f"Error handling drop: {str(e)}")

    def handle_drag_enter(self, event):
        """Visual feedback when dragging over"""
        self.configure(style='Custom.Hover.TLabel')

    def handle_drag_leave(self, event):
        """Reset visual feedback when leaving drag area"""
        self.configure(style='Custom.TLabel')

    def find_converter(self, widget):
        """Recursively find the ModernGifConverter instance"""
        if isinstance(widget, ModernGifConverter):
            return widget
        elif widget.master:
            return self.find_converter(widget.master)
        return None

    def validate_file(self, file_path: str) -> bool:
        """Validate dropped file type"""
        try:
            valid_extensions = ('.mp4', '.avi', '.mov', '.mkv', '.gif')
            return file_path.lower().endswith(valid_extensions)
        except Exception as e:
            print(f"Error validating file: {str(e)}")
            return False


class ModernGifConverter(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.max_parallel_jobs = min(os.cpu_count() or 1, 4)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_parallel_jobs)
        self.is_converting = False
        self.cancellation_event = threading.Event()
        self.style = ttk.Style("darkly")
        self.setup_styles()
        self.create_widgets()

    def create_batch_processing_ui(self):
        """Create the batch processing UI elements"""
        # Create batch frame as a direct child of the panel_content frame
        # This ensures proper positioning and avoids layout conflicts
        self.batch_frame = BatchProcessingFrame(self.panel_content, self)
        self.batch_frame.grid(row=0, column=0, sticky='nsew')
        self.batch_frame.grid_remove()  # Initially hidden

        # Configure the batch frame to expand properly
        self.panel_content.grid_rowconfigure(0, weight=1)
        self.panel_content.grid_columnconfigure(0, weight=1)

    def toggle_batch_ui(self):
        """Toggle the visibility of the batch processing UI"""
        if self.batch_frame.winfo_viewable():
            # Switch to log tab which will hide the batch frame
            self.switch_tab("log")
            self.batch_toggle_button.configure(text="Show Batch Processing")
            # Change Convert button text back when batch mode is hidden
            self.convert_button.configure(text="Convert")
        else:
            # Switch to batch tab which will show the batch frame
            self.switch_tab("batch")
            self.batch_toggle_button.configure(text="Hide Batch Processing")
            # Update the Convert button text based on the number of files
            self.batch_frame.update_convert_button_text()

        # Update the window size to fit the content
        self.update_idletasks()

        # Get the root window to resize it
        root = self.winfo_toplevel()
        new_height = root.winfo_reqheight()
        current_width = root.winfo_width()
        root.geometry(f"{current_width}x{new_height}")

    def add_to_batch(self, file_path: str):
        """Add a file to the batch queue"""
        try:
            # Make sure the batch frame exists
            if not hasattr(self, 'batch_frame'):
                self.create_batch_processing_ui()

            # Make sure the batch frame is visible by switching to batch tab
            if not self.batch_frame.winfo_viewable():
                self.switch_tab("batch")
                # Update Convert button text to Process Batch
                self.convert_button.configure(text="Process Batch")

            # If this is the first file and we have a selected file already,
            # add the selected file to the queue first
            if len(self.batch_frame.file_queue) == 0 and self.selected_file and self.selected_file != file_path:
                self.batch_frame.add_file_to_queue(self.selected_file)

            # Now add the new file
            if self.batch_frame.add_file_to_queue(file_path):
                filename = os.path.basename(file_path)
                self.file_label.configure(
                    text=f"Added to queue: {filename}",
                    foreground='#995FB6'
                )
            else:
                filename = os.path.basename(file_path)
                self.file_label.configure(
                    text=f"Already in queue: {filename}",
                    foreground='#ff9900'
                )

        except Exception as e:
            print(f"Error adding file to batch: {str(e)}")
            messagebox.showerror("Error", f"Failed to add file to batch: {str(e)}")

    # 4. Update the set_file method to use add_to_batch when appropriate

    def set_file(self, file_path: str, bypass_batch: bool = False):
        """
        Set the selected file and update the UI

        Args:
            file_path: Path to the file to convert
            bypass_batch: If True, always set as current file even if batch is active
        """
        try:
            # If we already have a batch queue with files and bypass_batch is False, add to it
            if not bypass_batch and hasattr(self, 'batch_frame') and self.batch_frame.winfo_viewable():
                self.add_to_batch(file_path)
                return

            # If we already have a selected file and bypass_batch is False, start a batch
            if not bypass_batch and self.selected_file and self.selected_file != file_path:
                self.add_to_batch(file_path)
                return

            # Normal behavior - set as the current file
            self.selected_file = file_path
            filename = os.path.basename(file_path)

            self.file_label.configure(
                text=f"Selected: {filename}",
                foreground='#995FB6'
            )

            # Update the UI immediately
            self.update()

        except Exception as e:
            print(f"Error setting file: {str(e)}")
            messagebox.showerror("Error", f"Failed to set file: {str(e)}")

    def start_batch_conversion(self, callback):
        """Start conversion for batch processing with callback"""
        if not self.check_dependencies():
            callback(False)
            return

        if not self.selected_file:
            messagebox.showerror("Error", "No file selected.")
            callback(False)
            return

        size_input = self.size_entry.get().strip()
        if size_input:
            try:
                desired_size = int(size_input)
                if desired_size <= 0:
                    raise ValueError()
            except ValueError:
                messagebox.showerror("Error", "Please enter a valid size in KB.")
                callback(False)
                return
        else:
            # No size limit specified - will use max quality settings
            desired_size = None

        # Make sure the smart panel is visible, but don't switch tabs if already showing
        if not self.smart_panel.winfo_viewable():
            # Only if the smart panel is completely hidden, show it with the log
            self.show_log()
        elif not self.log_frame.winfo_viewable() and not self.batch_frame.winfo_viewable():
            # If neither tab is visible but smart panel is, make sure at least one tab is visible
            # without changing the current tab
            self.log_frame.grid(row=0, column=0, sticky='nsew')

        # Do not disable the convert button during batch processing
        # as it needs to remain active to allow stopping the process
        
        # Store the callback
        self.batch_callback = callback

        # Verify we're using the right file
        self.log(f"Starting batch conversion for file: {os.path.basename(self.selected_file)}")

        # Set flag to suppress dialogs during batch processing
        self.suppress_dialogs = True

        # Reset cancellation flag
        self.cancellation_event.clear()

        # Start conversion in a separate thread
        self.is_converting = True
        self.conversion_thread = threading.Thread(
            target=self.run_batch_conversion,
            args=(self.selected_file, desired_size),
            daemon=True
        )
        self.conversion_thread.start()

    def run_batch_conversion(self, input_path, desired_size):
        """Run conversion for batch processing"""
        success = False
        output_file_size = 0
        try:
            self.log(f"Running batch conversion for {os.path.basename(input_path)}")

            # Create event loop in this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Run the conversion
            output_path = os.path.splitext(input_path)[0] + '_optimized.gif'
            success, file_size = loop.run_until_complete(
                self.convert_to_gif_batch(input_path, desired_size)
            )

            # Log the completion status
            if success:
                self.log(f"Batch conversion succeeded: {file_size / 1024:.1f} KB")
            else:
                self.log("Batch conversion failed")

        except Exception as e:
            self.log(f"Error during batch conversion: {str(e)}")
            success = False
            file_size = 0

        finally:
            # Only set is_converting to false, but don't modify the button
            # as we're still in batch processing mode
            self.is_converting = False
            
            # Ensure the button stays in "Stop" state if batch processing is still active
            if hasattr(self, 'batch_frame') and self.batch_frame.is_processing_batch:
                # Use after(0) to ensure this happens at the end of the event loop
                self.after(0, lambda: self.convert_button.configure(
                    text="Stop",
                    style='Danger.TButton'
                ))
            
            # Call the callback with success status and file size
            if hasattr(self, 'batch_callback') and self.batch_callback:
                output_path = os.path.splitext(input_path)[0] + '_optimized.gif'
                # Use master.after to ensure this runs in the main thread
                self.master.after(10, lambda: self.batch_callback(success, output_path, file_size))
            else:
                self.log("Warning: No batch callback found")

            # Clear suppress_dialogs flag
            self.suppress_dialogs = False

    # 3. Update convert_to_gif_batch to return both success status and output path
    async def convert_to_gif_batch(self, input_path, desired_size):
        """Modified conversion method that returns success/failure for batch processing"""
        self.log(f"Starting convert_to_gif_batch for {os.path.basename(input_path)}")
        # Reuse the existing convert_to_gif method, but track success
        try:
            # Call the original method with suppress_dialogs already set
            await self.convert_to_gif(input_path, desired_size)

            # Determine success - check if output file exists
            output_path = os.path.splitext(input_path)[0] + '_optimized.gif'
            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                self.log(f"Batch conversion successful, created: {output_path} ({file_size / 1024:.1f} KB)")
                return True, file_size

            self.log("Batch conversion failed - output file not found")
            return False, 0

        except Exception as e:
            self.log(f"Exception in batch conversion: {str(e)}")
            return False, 0

    @staticmethod
    def run_subprocess_hidden(command: list, **kwargs):
        """
        Run a subprocess with output hidden (no console window), capturing output.
        Works on both Windows and macOS (including ARM Macs).
        """
        if not isinstance(command, list) or not command:
            raise ValueError("Invalid command format; must be a non-empty list")
        binary_name = os.path.basename(command[0]).lower()
        allowed_binaries = {'ffmpeg', 'gifski', 'gifsicle', 'ffprobe', 'magick'}
        if binary_name.split('.')[0] not in allowed_binaries:
            raise ValueError(f"Unauthorized binary: {binary_name}")

        # Windows: hide console window using STARTUPINFO and creation flags
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            kwargs['startupinfo'] = startupinfo
            # Use CREATE_NO_WINDOW (and SW_HIDE if frozen) to ensure no visible console
            if getattr(sys, 'frozen', False):
                kwargs['creationflags'] = CREATE_NO_WINDOW | SW_HIDE
            else:
                kwargs['creationflags'] = CREATE_NO_WINDOW

        # macOS (or other Unix): ensure the binary is executable
        else:
            if os.path.exists(command[0]):
                try:
                    os.chmod(command[0], 0o755)  # make sure the binary is executable
                    # Also ensure the containing directory has execute permission (allow traversal)
                    binary_dir = os.path.dirname(command[0])
                    if binary_dir:
                        os.chmod(binary_dir, 0o755)
                except Exception as e:
                    print(f"Warning: Could not set executable permissions on {binary_name}: {e}")
            # On macOS ARM64, warn if an x86_64 binary is being used (optional diagnostic)
            if sys.platform == 'darwin' and platform.machine() == 'arm64':
                try:
                    result = subprocess.run(['file', command[0]], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True)
                    # If the output of `file` does not mention 'arm64' or 'Mach-O 64-bit arm64', it might be an Intel binary
                    if result.returncode == 0 and 'arm64' not in result.stdout and 'Rosetta' not in result.stdout:
                        print(f"Warning: {binary_name} may not be ARM64-optimized (running via Rosetta)")
                except Exception:
                    pass  # If the `file` command isn’t available or fails, ignore this check

        # If the command is ffprobe, we need its output (do not create a temp file, just capture output directly)
        if os.path.basename(command[0]).lower().startswith('ffprobe'):
            kwargs.setdefault('stdout', subprocess.PIPE)
            kwargs.setdefault('stderr', subprocess.PIPE)
            return subprocess.run(command, **kwargs)

        # For other commands, run with stdout/stderr hidden by default
        kwargs.setdefault('stdout', subprocess.PIPE)
        kwargs.setdefault('stderr', subprocess.PIPE)
        temp_dir = None
        original_output = None
        try:
            # If an output file is specified with -o/--output, write to a temp file to avoid partial results on failure
            if '-o' in command or '--output' in command:
                temp_dir = tempfile.mkdtemp(prefix='giflight_')
                # Locate the output path argument and replace it with a temp path
                out_flag = '-o' if '-o' in command else '--output'
                idx = command.index(out_flag) + 1
                if idx < len(command):
                    original_output = command[idx]
                    temp_output = os.path.join(temp_dir, os.path.basename(original_output))
                    command[idx] = temp_output
            # Run the subprocess
            result = subprocess.run(command, **kwargs)
            # If we wrote to a temp file and it exists, move it to the intended location
            if temp_dir and original_output and os.path.isfile(temp_output):
                shutil.move(temp_output, original_output)
            return result
        finally:
            # Clean up any temporary directory
            if temp_dir and os.path.isdir(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    print(f"Error cleaning up temporary directory: {e}")

    async def apply_imagemagick_optimization(self, input_path: str, output_path: str) -> str:
        """
        Apply an ImageMagick optimization pass to reduce GIF size.
        Returns the path to the optimized GIF.
        """
        temp_output = None
        try:
            # Get original file size before optimization
            original_size = os.path.getsize(input_path)
            self.log(
                f"Applying final optimization pass with ImageMagick (original size: {original_size / 1024:.1f} KB)...")

            temp_dir = os.path.dirname(input_path)
            temp_output = os.path.join(temp_dir, 'imagemagick_temp.gif')

            # Get the current ImageMagick path
            imagemagick_path = IMAGEMAGICK_PATH

            # Set environment variable to suppress OpenMP runtime duplication error
            if sys.platform == 'darwin':
                os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

            command = [
                imagemagick_path,
                input_path,
                '-coalesce',
                '-layers', 'optimize',
                '-fuzz', '0%',
                '-layers', 'optimize-transparency',
                '-quiet',
                temp_output
            ]

            # Platform-specific subprocess settings
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                creationflags = CREATE_NO_WINDOW | SW_HIDE if getattr(sys, 'frozen',
                                                                      False) else CREATE_NO_WINDOW
                process = await asyncio.create_subprocess_exec(
                    *command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    startupinfo=startupinfo, creationflags=creationflags
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    *command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )

            # Set up progress indicator
            max_wait_time = 30  # Maximum expected seconds for optimization
            progress_steps = 20  # Number of progress indicators to show

            # Start animation loop
            for i in range(progress_steps):
                if process.returncode is not None:
                    break

                progress = i / progress_steps
                bar_width = 15
                filled_length = int(bar_width * progress)
                bar = '█' * filled_length + '░' * (bar_width - filled_length)
                self.log(f"Optimizing: [{bar}] {int(progress * 100)}%", replace_last=True)

                try:
                    # Wait a bit, but allow for early termination if process completes
                    await asyncio.wait_for(asyncio.shield(process.wait()), timeout=max_wait_time / progress_steps)
                    break
                except asyncio.TimeoutError:
                    # Process still running, continue animation
                    pass

            # Wait for completion
            stdout, stderr = await process.communicate()

            # Completion progress bar
            self.log(f"Optimizing: [{'█' * bar_width}] 100%", replace_last=True)

            # Check for errors
            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "No error details available"
                raise RuntimeError(f"Optimization failed: {error_msg}")

            if not os.path.exists(temp_output):
                raise RuntimeError("ImageMagick did not produce an output file")

            # Replace the original output file with the optimized result
            shutil.move(temp_output, output_path)

            # Get final size for logging
            if os.path.exists(output_path):
                final_size = os.path.getsize(output_path)
                size_reduction = original_size - final_size
                reduction_percent = (size_reduction / original_size) * 100 if original_size > 0 else 0
                self.log(
                    f"✓ Final optimization complete: {original_size / 1024:.1f} KB → {final_size / 1024:.1f} KB ({reduction_percent:.1f}% reduction)")

            return output_path
        finally:
            # Cleanup: remove temp file if it still exists
            if temp_output and os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except Exception as e:
                    self.log(f"Error removing temporary file: {e}")

    def toggle_conversion(self):
        """Toggle between starting and stopping conversion"""
        if not self.is_converting:
            # Start conversion
            if not self.check_dependencies():
                return

            # Check if batch processing UI is visible and has files in queue
            if self.batch_frame.winfo_viewable() and self.batch_frame.file_queue:
                # Start batch processing instead
                self.batch_frame.toggle_batch_processing()
                return

            if not self.selected_file:
                messagebox.showerror("Error", "Please select a file first.")
                return

            size_input = self.size_entry.get().strip()
            if size_input:
                try:
                    desired_size = int(size_input)
                    if desired_size <= 0:
                        raise ValueError()
                except ValueError:
                    messagebox.showerror("Error", "Please enter a valid size in KB.")
                    return
            else:
                desired_size = None

            # Reset cancellation flag
            self.cancellation_event.clear()

            # Update button appearance
            self.convert_button.configure(
                text="Stop",
                style='Danger.TButton'
            )

            # For batch mode (2+ files), show the full tabbed interface
            if hasattr(self, 'batch_frame') and self.batch_frame and len(self.batch_frame.file_queue) >= 2:
                self.show_log()
            else:
                # For single file conversion, show just the log console without tabs
                self.show_log_without_tabs()

            self.is_converting = True

            # Start conversion in a separate thread
            self.conversion_thread = threading.Thread(
                target=self.run_conversion,
                args=(self.selected_file, desired_size),
                daemon=True
            )
            self.conversion_thread.start()
        else:
            # Stop conversion
            if self.batch_frame.is_processing_batch:
                self.batch_frame.stop_batch_processing()
            else:
                self.stop_conversion()

    def stop_conversion(self):
        """Stop the ongoing conversion process"""
        if self.is_converting:
            self.log("\nStopping conversion...")
            self.cancellation_event.set()

            # Don't disable the button, just change its appearance
            self.convert_button.configure(
                text="Stopping...",
                style='Danger.TButton'
            )

            # Wait for conversion thread to finish in a separate thread
            threading.Thread(target=self.wait_for_conversion_end, daemon=True).start()

    def wait_for_conversion_end(self):
        """Wait for conversion to end and restore button state"""
        if hasattr(self, 'conversion_thread'):
            self.conversion_thread.join()

        # Only reset button if we're not in batch processing mode
        # or if batch processing has completed
        if not self.batch_frame.is_processing_batch:
            # Reset button state and text
            if self.batch_frame.winfo_viewable() and len(self.batch_frame.file_queue) >= 2:
                button_text = "Process Batch"
            else:
                button_text = "Convert"

            self.convert_button.configure(
                text=button_text,
                style='Primary.TButton',
                state='normal'
            )
        
        self.is_converting = False
        self.update()

    def setup_styles(self):
        """Configure custom styles for widgets"""
        # Main background color
        self.style = ttk.Style("darkly")
        self.style.configure('TFrame', background='#1a1a1a')

        # Basic label style (important to set this first)
        self.style.configure('TLabel',
                             background='#1a1a1a',
                             foreground='#ffffff')

        # Drop zone container style
        self.style.configure('DropZone.TFrame',
                             background='#1a1a1a',
                             borderwidth=2,
                             relief='solid')

        # Custom style specifically for the drop label - default state
        self.style.configure('Custom.TLabel',
                             background='#2d2d2d',
                             foreground='#ffffff',
                             font=('Segoe UI', 12),
                             borderwidth=2,
                             relief='solid',
                             padding=20,
                             anchor='center',
                             justify='center')

        # Hover state style also needs the centering
        self.style.configure('Custom.Hover.TLabel',
                             background='#3d3d3d',
                             foreground='#ffffff',
                             font=('Segoe UI', 12, 'bold'),
                             borderwidth=2,
                             relief='solid',
                             padding=20,
                             anchor='center',
                             justify='center')

        # Rest of your styles...
        self.style.configure('Success.TLabel',
                             background='#1a1a1a',
                             foreground='#28a745')

        self.style.configure('Error.TLabel',
                             background='#1a1a1a',
                             foreground='#dc3545')

        self.style.configure('Primary.TButton',
                             background='#007bff',
                             foreground='white',
                             font=('Segoe UI', 10, 'bold'),
                             padding=(5, 2))

        self.style.configure('TEntry',
                             fieldbackground='#2d2d2d',
                             foreground='white',
                             insertcolor='white')

        self.style.configure('TProgressbar',
                             background='#007bff',
                             troughcolor='#2d2d2d',
                             borderwidth=0,
                             thickness=10)

        # Add new style for Stop button
        self.style.configure('Danger.TButton',
                             background='#dc3545',
                             foreground='white',
                             font=('Segoe UI', 10, 'bold'),
                             padding=(5, 2))

    # Find all references to content_frame in the create_widgets method
    # and update them to self.content_frame

    def create_widgets(self):
        """Create and arrange all GUI widgets"""
        self.grid(sticky='nsew', padx=20, pady=20)
        self.grid_columnconfigure(0, weight=1)

        # Logo frame
        logo_frame = ttk.Frame(self)
        logo_frame.grid(row=0, column=0, pady=(0, 0))

        # Load and display animated logo
        try:
            logo_path = get_resource_path('logo.gif')  # Changed from logo.png to logo.gif
            self.gif = Image.open(logo_path)

            # Get the number of frames in the GIF
            self.gif_frames = []
            self.current_frame = 0

            try:
                while True:
                    # Copy the current frame
                    frame = self.gif.copy()
                    # Convert to RGBA if necessary and resize
                    if frame.mode != 'RGBA':
                        frame = frame.convert('RGBA')
                    frame = frame.resize((470, 185), Image.Resampling.LANCZOS)
                    # Create PhotoImage and store it
                    photo = ImageTk.PhotoImage(frame)
                    self.gif_frames.append(photo)
                    # Move to next frame
                    self.gif.seek(len(self.gif_frames))
            except EOFError:
                pass  # We've hit the end of the frames

            # Get frame durations (in milliseconds)
            self.frame_durations = []
            for frame in range(len(self.gif_frames)):
                self.gif.seek(frame)
                self.frame_durations.append(self.gif.info.get('duration', 100))  # Default to 100ms if not specified

            # Create label for displaying the animated logo
            self.logo_label = ttk.Label(logo_frame)
            self.logo_label.grid(pady=(0, 10))

            # Start the animation
            self.animate_logo()

        except Exception as e:
            print(f"Error loading logo: {str(e)}")
            # Fallback if logo file is missing
            self.logo_label = ttk.Label(logo_frame, text="GIF Converter",
                                        font=('Segoe UI', 32, 'bold'))
            self.logo_label.grid(pady=(0, 10))

        # Main content frame
        self.content_frame = ttk.Frame(self, style='TFrame')
        self.content_frame.grid(row=1, column=0, sticky='nsew')
        self.content_frame.grid_columnconfigure(0, weight=1)

        # Container frame for drop zone with fixed size and padding
        drop_container = ttk.Frame(self.content_frame, style='DropZone.TFrame')
        drop_container.grid(row=0, column=0, sticky='ew', pady=(0, 20), padx=20)
        drop_container.grid_columnconfigure(0, weight=1)

        # Drag & Drop zone with centered text
        self.drop_label = DragDropLabel(
            drop_container,
            text="Drop your video or GIF here\n\nSupports MP4, AVI, MOV, MKV, and GIF",
            style='Custom.TLabel',
            anchor='center',  # Add anchor='center'
            padding=20
        )
        self.drop_label.grid(row=0, column=0, sticky='nsew', pady=20, padx=20)

        # Configure the grid of the drop_container to expand the label
        drop_container.grid_rowconfigure(0, weight=1)
        drop_container.grid_columnconfigure(0, weight=1)

        # File info label
        self.file_label = ttk.Label(
            self.content_frame,
            text="No file selected",
            font=('Segoe UI', 10),
            foreground='#888888'
        )
        self.file_label.grid(row=1, column=0, pady=(0, 10))

        # Size input frame with centering
        size_frame = ttk.Frame(self.content_frame)
        size_frame.grid(row=2, column=0, sticky='ew', pady=(0, 20))
        size_frame.grid_columnconfigure(0, weight=1)  # Left spacer
        size_frame.grid_columnconfigure(1, weight=0)  # Label
        size_frame.grid_columnconfigure(2, weight=0)  # Entry
        size_frame.grid_columnconfigure(3, weight=1)  # Right spacer

        # Empty frame for left spacing
        ttk.Frame(size_frame).grid(row=0, column=0)

        size_label = ttk.Label(
            size_frame,
            text="Desired GIF Size (KB):",
            font=('Segoe UI', 10)
        )
        size_label.grid(row=0, column=1, padx=(0, 10))

        self.size_entry = ttk.Entry(size_frame, width=10)
        self.size_entry.grid(row=0, column=2)

        # Empty frame for right spacing
        ttk.Frame(size_frame).grid(row=0, column=3)

        # Create a frame for the buttons so they can be placed side by side
        button_frame = ttk.Frame(self.content_frame)
        button_frame.grid(row=3, column=0, pady=(0, 10))

        # Convert button
        self.convert_button = ttk.Button(
            button_frame,
            text="Convert",
            style='Primary.TButton',
            command=self.toggle_conversion
        )
        self.convert_button.grid(row=0, column=0, padx=(0, 10))

        # Options button
        self.options_button = ttk.Button(
            button_frame,
            text="Options",
            style='Primary.TButton',
            command=self.show_options
        )
        self.options_button.grid(row=0, column=1)

        # Create smart panel container - will hold logs and batch processing in a tabbed interface
        self.smart_panel = ttk.Frame(self.content_frame)
        self.smart_panel.grid(row=4, column=0, sticky='nsew', pady=(0, 5))
        self.smart_panel.grid_columnconfigure(0, weight=1)
        self.smart_panel.grid_rowconfigure(1, weight=1)

        # Tab buttons frame
        self.tab_buttons_frame = ttk.Frame(self.smart_panel)
        self.tab_buttons_frame.grid(row=0, column=0, sticky='ew')
        self.tab_buttons_frame.grid_columnconfigure(0, weight=1)
        self.tab_buttons_frame.grid_columnconfigure(1, weight=1)

        # Create styled tab buttons
        self.log_tab_button = ttk.Button(
            self.tab_buttons_frame,
            text="Log Console",
            style='Tab.TButton',
            command=lambda: self.switch_tab("log")
        )
        self.log_tab_button.grid(row=0, column=0, sticky='ew', padx=(0, 2))

        self.batch_tab_button = ttk.Button(
            self.tab_buttons_frame,
            text="Batch Processing",
            style='Tab.TButton',
            command=lambda: self.switch_tab("batch")
        )
        self.batch_tab_button.grid(row=0, column=1, sticky='ew', padx=(2, 0))

        # Panel content frame
        self.panel_content = ttk.Frame(self.smart_panel)
        self.panel_content.grid(row=1, column=0, sticky='nsew')
        self.panel_content.grid_columnconfigure(0, weight=1)
        self.panel_content.grid_rowconfigure(0, weight=1)

        # Create Log frame
        self.log_frame = ttk.Frame(self.panel_content)
        self.log_frame.grid(row=0, column=0, sticky='nsew')
        self.log_frame.grid_columnconfigure(0, weight=1)
        self.log_frame.grid_rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            self.log_frame,
            height=10,
            width=50,
            bg='#2d2d2d',
            fg='white',
            font=('Consolas', 10),
            wrap='word',
            borderwidth=1,
            relief='solid'
        )
        scrollbar = ttk.Scrollbar(self.log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.log_text.grid(row=0, column=0, sticky='nsew', padx=(0, 2))
        scrollbar.grid(row=0, column=1, sticky='ns')

        # Create batch frame (will be populated in create_batch_processing_ui)
        self.batch_frame = None

        # Initially hide log frame
        self.log_frame.grid_remove()

        # Visual indicator for the smart panel (pulsing border)
        self.smart_panel_indicator = ttk.Frame(self.smart_panel, style='Indicator.TFrame')
        self.smart_panel_indicator.place(relwidth=1, relheight=1, x=0, y=0)
        self.smart_panel_indicator.lower()  # Place it behind other widgets

        # Initially hide the smart panel
        self.smart_panel.grid_remove()

        # Current active tab
        self.active_tab = None

        # Animation variables
        self.pulse_alpha = 0
        self.pulse_increasing = True
        self.pulse_animation_active = False

        # Store the selected file path
        self.selected_file: Optional[str] = None

        # Initialize batch processing UI
        self.create_batch_processing_ui()

    def show_options(self):
        """Open the options window"""
        OptionsWindow(self)

    def animate_logo(self):
        """Animate the logo GIF with proper error handling for cross-platform compatibility"""
        if not hasattr(self, 'gif_frames') or not self.gif_frames:
            return

        try:
            # Update the frame
            self.logo_label.configure(image=self.gif_frames[self.current_frame])
            # Get frame duration (with fallback to 100ms)
            frame_duration = 100
            if hasattr(self, 'frame_durations') and self.frame_durations and len(
                    self.frame_durations) > self.current_frame:
                frame_duration = self.frame_durations[self.current_frame]

            # Schedule the next frame update
            self.current_frame = (self.current_frame + 1) % max(1, len(self.gif_frames))
            self.after(frame_duration, self.animate_logo)
        except Exception as e:
            print(f"Error animating logo: {e}")
            # Just schedule next frame in case of error
            self.current_frame = (self.current_frame + 1) % max(1, len(self.gif_frames))
            self.after(100, self.animate_logo)

    def show_log(self):
        """Show the log area"""
        # Make sure the smart panel is visible
        if not self.smart_panel.winfo_viewable():
            self.smart_panel.grid()

        # Switch to the log tab
        if hasattr(self, 'log_tab_button'):
            self.log_tab_button.configure(style='ActiveTab.TButton')
            if hasattr(self, 'batch_tab_button'):
                self.batch_tab_button.configure(style='Tab.TButton')

        # Show log frame
        self.log_frame.grid(row=0, column=0, sticky='nsew')

        # Make sure batch frame is hidden if it exists
        if hasattr(self, 'batch_frame') and self.batch_frame and self.batch_frame.winfo_viewable():
            self.batch_frame.grid_remove()

        # Update the window to fit height while maintaining width
        self.update_idletasks()  # Let the window process the new widget
        root = self.winfo_toplevel()
        new_height = root.winfo_reqheight()  # Get required height
        current_width = root.winfo_width()
        root.geometry(f"{current_width}x{new_height}")  # Set new size

    def show_log_without_tabs(self):
        """Show only the log area without tab buttons for single file conversion"""
        # Make sure the smart panel is visible
        if not self.smart_panel.winfo_viewable():
            self.smart_panel.grid()

        # Hide the tab buttons frame
        if hasattr(self, 'tab_buttons_frame'):
            self.tab_buttons_frame.grid_remove()

        # Make sure batch frame is hidden if it exists
        if hasattr(self, 'batch_frame') and self.batch_frame:
            self.batch_frame.grid_remove()

        # Show log frame - do this after hiding other frames to ensure proper stacking
        self.log_frame.grid(row=0, column=0, sticky='nsew')
        self.log_frame.tkraise()  # Ensure log frame is on top

        # Clear any previously active tab state
        if hasattr(self, 'active_tab'):
            self.active_tab = None

        # Update the window to fit height while maintaining width
        self.update_idletasks()  # Let the window process the new widget
        root = self.winfo_toplevel()
        new_height = root.winfo_reqheight()  # Get required height
        current_width = root.winfo_width()
        root.geometry(f"{current_width}x{new_height}")  # Set new size

    def log(self, message: str, replace_last: bool = False):
        """Add message to log area"""
        if replace_last:
            # Delete last line
            self.log_text.delete("end-2c linestart", "end-1c")
        self.log_text.insert('end', message + '\n')
        self.log_text.see('end')
        
        # Only update the UI occasionally for progress messages to reduce jittering
        if not replace_last or not message.startswith("Processing frames:"):
            self.update()

    def check_dependencies(self):
        missing = []
        if not os.path.exists(FFMPEG_PATH):
            missing.append("FFmpeg")
        if not os.path.exists(GIFSKI_PATH):
            missing.append("Gifski")
        if not os.path.exists(GIFSICLE_PATH):
            missing.append("Gifsicle")
        if not os.path.exists(IMAGEMAGICK_PATH):
            missing.append("ImageMagick")

        if missing:
            messagebox.showerror(
                "Missing Dependencies",
                f"The following required programs are missing or incorrectly configured:\n"
                f"{', '.join(missing)}\n\n"
                f"Please ensure they are installed and the paths are correctly set."
            )
            return False
        return True

    def start_conversion(self):
        """Start the conversion process"""
        if not self.check_dependencies():
            return

        if not self.selected_file:
            messagebox.showerror("Error", "Please select a file first.")
            return

        size_input = self.size_entry.get().strip()
        if size_input:
            try:
                desired_size = int(size_input)
                if desired_size <= 0:
                    raise ValueError()
            except ValueError:
                messagebox.showerror("Error", "Please enter a valid size in KB.")
                return
        else:
            desired_size = None

        # Show log area
        self.show_log()
        self.convert_button.configure(state='disabled')

        # Start conversion in a separate thread
        self.conversion_thread = threading.Thread(
            target=self.run_conversion,
            args=(self.selected_file, desired_size),
            daemon=True
        )
        self.conversion_thread.start()

    def run_conversion(self, input_path: str, desired_size: int):
        """Run the conversion process"""
        try:
            # Create event loop in this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Run the conversion
            loop.run_until_complete(
                self.convert_to_gif(input_path, desired_size)
            )

        except Exception as e:
            self.log(f"Error during conversion: {str(e)}")

        finally:
            self.convert_button.configure(state='normal')

    async def run_in_executor(self, func, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, func, *args)

    async def run_subprocess(self, command: list):
        """
        Launch a subprocess asynchronously, capturing its output.
        Adjusts creation flags on Windows to hide console, and ensures executables
        have proper permissions on macOS.
        """
        # Base subprocess settings: capture stdout and stderr
        subprocess_kwargs = {
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE
        }

        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            subprocess_kwargs['startupinfo'] = startupinfo
            # Use CREATE_NO_WINDOW (and SW_HIDE if frozen) to ensure no visible console
            if getattr(sys, 'frozen', False):
                subprocess_kwargs['creationflags'] = CREATE_NO_WINDOW | SW_HIDE
            else:
                subprocess_kwargs['creationflags'] = CREATE_NO_WINDOW

        # macOS (or other Unix): ensure the target binary is executable
        else:
            if command and os.path.exists(command[0]):
                try:
                    os.chmod(command[0], 0o755)
                except Exception as e:
                    print(f"Warning: Could not set executable permissions on {command[0]}: {e}")
        # Run the process asynchronously
        process = await asyncio.create_subprocess_exec(*command, **subprocess_kwargs)
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            # Decode stderr in case of error to include the message
            err_msg = stderr.decode() if stderr else "Unknown error"
            raise RuntimeError(f"Process failed: {err_msg}")
        return stdout.decode() if stdout else ""

    # Update the extract_frames method
    def extract_frames(self, video_path: str, frames_dir: str, fps: int):
        """
        Extracts frames from a video using FFmpeg. Ensures FFmpeg runs with hidden
        console on Windows and is executable on macOS.
        """
        ffmpeg_cmd = [
            FFMPEG_PATH,
            '-i', video_path,
            '-vf', f'fps={fps}',
            '-pix_fmt', 'rgba',
            os.path.join(frames_dir, 'frame_%04d.png')
        ]
        # Setup to capture output (suppress console)
        subprocess_kwargs = {
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE,
            'text': True  # capture text output for error parsing
        }
        if sys.platform == 'win32':
            # Hide FFmpeg console window on Windows
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            subprocess_kwargs['startupinfo'] = startupinfo
            if getattr(sys, 'frozen', False):
                subprocess_kwargs['creationflags'] = CREATE_NO_WINDOW | SW_HIDE
            else:
                subprocess_kwargs['creationflags'] = CREATE_NO_WINDOW
        else:
            # On macOS, ensure ffmpeg binary is executable
            try:
                os.chmod(FFMPEG_PATH, 0o755)
            except Exception as e:
                print(f"Warning: Could not set executable permissions on FFmpeg: {e}")
        # Run FFmpeg
        result = subprocess.run(ffmpeg_cmd, **subprocess_kwargs)
        if result.returncode != 0:
            # If FFmpeg failed, raise an error with its stderr output
            raise RuntimeError(f"FFmpeg Error: {result.stderr}")

    def extract_gif_frames(self, gif_path, frames_dir):
        """Extract frames from an existing GIF file"""
        try:
            gif = Image.open(gif_path)
            frame_count = 0
            durations = []

            try:
                while True:
                    durations.append(gif.info['duration'])
                    gif.seek(gif.tell() + 1)
            except EOFError:
                pass  # We've hit the end of the frames

            gif.seek(0)

            try:
                while True:
                    frame = gif.convert('RGBA')
                    frame.save(os.path.join(frames_dir, f'frame_{frame_count:04d}.png'))
                    frame_count += 1
                    gif.seek(gif.tell() + 1)
            except EOFError:
                pass

            return frame_count, sum(durations) / len(durations) if durations else 100
        except Exception as e:
            raise RuntimeError(f"Failed to extract frames from GIF: {str(e)}")

    def get_video_fps(self, video_path: str) -> int:
        """
        Determine the FPS of a video using FFmpeg. Runs FFmpeg with no visible console
        on Windows and proper permissions on macOS.
        """
        ffmpeg_cmd = [FFMPEG_PATH, '-i', video_path, '-hide_banner']
        subprocess_kwargs = {
            'stderr': subprocess.PIPE,
            'text': True
        }
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            subprocess_kwargs['startupinfo'] = startupinfo
            subprocess_kwargs['creationflags'] = CREATE_NO_WINDOW | SW_HIDE if getattr(sys, 'frozen',
                                                                                       False) else CREATE_NO_WINDOW
        else:
            try:
                os.chmod(FFMPEG_PATH, 0o755)
            except Exception as e:
                print(f"Warning: Could not set executable permissions on FFmpeg: {e}")
        result = subprocess.run(ffmpeg_cmd, **subprocess_kwargs)
        output = result.stderr  # FFmpeg prints info (including FPS) to stderr
        # Parse the output for an "XX fps" pattern or timebase info
        import re
        fps_match = re.search(r'(\d+(?:\.\d+)?)\s*fps', output)
        tb_match = re.search(r'tbr,\s*(\d+(\.\d+)?) tbn', output)  # alternate pattern
        if fps_match:
            fps_value = float(fps_match.group(1))
            self.log(f"Detected FPS: {fps_value}")
            return round(fps_value)
        elif tb_match:
            # If fps not directly given, derive from timebase if present
            fps_value = float(tb_match.group(1))
            self.log(f"Calculated FPS from timebase: {fps_value}")
            return round(fps_value) if 1 <= fps_value <= 120 else 15
        else:
            self.log("Could not detect FPS, using default 24")
            return 24

    def get_subprocess_kwargs(self, binary_path=None):
        """Get platform-specific subprocess configuration"""
        kwargs = {
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE
        }

        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            kwargs['startupinfo'] = startupinfo
            # Use CREATE_NO_WINDOW (and SW_HIDE if frozen) to ensure no visible console
            if getattr(sys, 'frozen', False):
                kwargs['creationflags'] = CREATE_NO_WINDOW | SW_HIDE
            else:
                kwargs['creationflags'] = CREATE_NO_WINDOW

        # macOS (or other Unix): ensure the target binary is executable
        else:
            if binary_path and os.path.exists(binary_path):
                try:
                    os.chmod(binary_path, 0o755)
                except Exception as e:
                    print(f"Warning: Could not set executable permissions on {binary_path}: {e}")
        return kwargs

    def has_transparency(self, frames_dir: str) -> bool:
        """Check if any frame in the sequence has transparency"""
        try:
            frames = sorted([f for f in os.listdir(frames_dir) if f.endswith('.png')])
            if not frames:
                return False

            # Check the first frame
            first_frame_path = os.path.join(frames_dir, frames[0])
            first_frame = Image.open(first_frame_path)

            # If image is not RGBA, it has no transparency
            if first_frame.mode != 'RGBA':
                return False

            # Check if the alpha channel has any transparent pixels
            alpha = first_frame.split()[3]
            return alpha.getextrema()[0] < 255

        except Exception as e:
            self.log(f"Error checking transparency: {str(e)}")
            return False

    async def apply_delta_alpha_optimization(self, frames_dir: str) -> bool:
        """
        Optimizes alpha channel encoding by preserving the base transparency
        and only encoding actual changes in the alpha channel.

        The algorithm works by:
        1. Using the first frame as a reference for base transparency
        2. For subsequent frames, only storing alpha values that differ from the previous frame
        3. Maintaining a running "current alpha state" to track cumulative changes
        """
        try:
            from PIL import Image
            import os
            import numpy as np

            frames = sorted([f for f in os.listdir(frames_dir) if f.endswith('.png')])
            if not frames:
                self.log("No frames found for alpha optimization")
                return False

            # Load first frame and use it as reference
            first_frame_path = os.path.join(frames_dir, frames[0])
            with Image.open(first_frame_path) as first_frame:
                if first_frame.mode != 'RGBA':
                    first_frame = first_frame.convert('RGBA')

                base_alpha = np.array(first_frame.split()[3])

            total_frames = len(frames) - 1
            self.log(f"Optimizing alpha channel for {total_frames} frames...")

            # Process frames and track alpha changes
            prev_alpha = base_alpha.copy()
            total_changes = 0
            total_pixels = base_alpha.size

            # Process frames
            for i in range(1, len(frames)):
                if self.cancellation_event.is_set():
                    return False

                frame_path = os.path.join(frames_dir, frames[i])
                with Image.open(frame_path) as frame:
                    if frame.mode != 'RGBA':
                        frame = frame.convert('RGBA')

                    # Get the current frame's channels
                    r, g, b, curr_alpha = frame.split()
                    curr_alpha_array = np.array(curr_alpha)

                    # Detect actual changes in alpha
                    alpha_diff = curr_alpha_array != prev_alpha
                    change_count = np.sum(alpha_diff)
                    total_changes += change_count

                    if np.any(alpha_diff):
                        # Only update alpha values that have actually changed
                        new_alpha = prev_alpha.copy()
                        new_alpha[alpha_diff] = curr_alpha_array[alpha_diff]

                        # Create new frame with updated alpha
                        new_alpha_img = Image.fromarray(new_alpha, mode='L')
                        new_frame = Image.merge('RGBA', (r, g, b, new_alpha_img))

                        # Save the modified frame, overwriting the original
                        new_frame.save(frame_path, 'PNG')

                        # Update previous alpha for next comparison
                        prev_alpha = new_alpha

                    # Log progress periodically
                    if i % 10 == 0 or i == total_frames:
                        avg_changes = (total_changes / (i * total_pixels)) * 100
                        self.log(f"Processed {i}/{total_frames} frames - Avg alpha changes: {avg_changes:.1f}%",
                                 replace_last=True)

            # Final statistics
            avg_changes_overall = (total_changes / (total_frames * total_pixels)) * 100
            self.log(f"✓ Alpha channel optimization complete - Average changes per frame: {avg_changes_overall:.1f}%")
            return True

        except Exception as e:
            self.log(f"Error during alpha optimization: {str(e)}")
            return False

    async def apply_transparency_mask(self, frames_dir: str, first_frame_path: str) -> bool:
        """
        Apply transparency handling to the frame sequence based on user settings.
        This method supports two different transparency modes:
        1. Delta-based animated transparency (preserves per-frame alpha changes)
        2. Uniform transparency (applies first frame's alpha to all frames)

        Args:
            frames_dir: Directory containing the frame sequence
            first_frame_path: Path to the first frame (used as reference for uniform transparency)

        Returns:
            bool: True if transparency processing was successful, False otherwise
        """
        try:
            # Load user settings to determine transparency handling mode
            settings = OptionsWindow.load_settings()
            preserve_animated_alpha = settings.get('preserve_animated_alpha', False)

            if preserve_animated_alpha:
                # Use delta-based optimization for animated transparency
                self.log("Applying delta-based alpha optimization...")
                return await self.apply_delta_alpha_optimization(frames_dir)

            # Original uniform transparency behavior
            # Load the alpha mask from the first frame
            first_frame = Image.open(first_frame_path)
            if first_frame.mode != 'RGBA':
                first_frame = first_frame.convert('RGBA')

            # Extract the alpha channel to use as a uniform mask
            alpha_mask = first_frame.split()[3]

            # Get all PNG frames in the directory
            frames = sorted([f for f in os.listdir(frames_dir) if f.endswith('.png')])
            remaining_frames = len(frames) - 1  # Subtract 1 since we already processed first frame

            self.log(f"Applying uniform transparency mask to {remaining_frames} frames...")

            # Process each remaining frame
            progress_update_interval = max(1, remaining_frames // 20)  # Update ~20 times total
            for idx, frame_file in enumerate(frames[1:], 1):
                # Check for user cancellation
                if self.cancellation_event.is_set():
                    return False

                # Only update progress display occasionally to reduce jittering
                if idx % progress_update_interval == 0 or idx == remaining_frames:
                    self.log(f"Processing frames: {idx}/{remaining_frames}", replace_last=True)

                # Load and process the current frame
                frame_path = os.path.join(frames_dir, frame_file)
                frame = Image.open(frame_path)

                # Ensure frame is in RGBA mode
                if frame.mode != 'RGBA':
                    frame = frame.convert('RGBA')

                # Split the frame into its color channels
                r, g, b, _ = frame.split()

                # Create new frame with original RGB but uniform alpha mask
                new_frame = Image.merge('RGBA', (r, g, b, alpha_mask))

                # Save the modified frame, overwriting the original
                new_frame.save(frame_path, 'PNG')

            self.log("✓ Transparency processing complete")
            return True

        except Exception as e:
            self.log(f"Error applying transparency mask: {str(e)}")
            return False

    async def prepare_frames_with_skip(self, source_dir: str, skip: int, batch_id: int, attempt_id: int) -> str:
        """Creates a new directory with frame-skipped copies of the original frames"""
        if skip <= 1:
            return source_dir

        try:
            source_dir = os.path.normpath(source_dir)
            parent_dir = os.path.dirname(source_dir)
            skip_dir = os.path.normpath(os.path.join(
                parent_dir,
                f'frames_skip_{skip}_batch_{batch_id}_attempt_{attempt_id}'
            ))

            if os.path.exists(skip_dir):
                shutil.rmtree(skip_dir)
            os.makedirs(skip_dir)

            frames = sorted([f for f in os.listdir(source_dir) if f.endswith('.png')])
            if not frames:
                self.log(f"No frames found in source directory: {source_dir}")
                return source_dir

            new_frame_index = 0
            processed_frames = 0

            for i, frame in enumerate(frames):
                if i % skip == 0:
                    source_path = os.path.normpath(os.path.join(source_dir, frame))
                    new_frame_name = f'frame_{new_frame_index:04d}.png'
                    dest_path = os.path.normpath(os.path.join(skip_dir, new_frame_name))

                    try:
                        shutil.copy2(source_path, dest_path)
                        new_frame_index += 1
                        processed_frames += 1
                    except Exception as e:
                        self.log(f"Error copying frame {frame}: {str(e)}")
                        continue

            if processed_frames == 0:
                self.log(f"No frames were successfully copied to skip directory")
                if os.path.exists(skip_dir):
                    shutil.rmtree(skip_dir)
                return source_dir

            return skip_dir

        except Exception as e:
            self.log(f"Error preparing skipped frames: {str(e)}")
            if 'skip_dir' in locals() and os.path.exists(skip_dir):
                shutil.rmtree(skip_dir)
            return source_dir

    # This is an update to try_optimization_params to fix the timeout issue and simplify progress indicators
    async def try_optimization_params(self, frames_dir: str, params: OptimizationParams,
                                      current_fps: float, batch_id: int, attempt_id: int) -> Tuple[int, str, str]:
        """
        Try a single optimization configuration.
        Returns a tuple containing:
        (optimized file size in bytes, output file path, skip directory path)
        """
        skip_dir = None
        temp_output = None
        temp_output_optimized = None

        # Load settings and override parameters if needed
        settings = OptionsWindow.load_settings()
        quality = 100 if settings.get('lock_quality', False) else params.quality
        lossy = 0 if settings.get('lock_lossy', False) else params.lossy
        frame_skip = 1 if settings.get('lock_frame_skip', False) else params.frame_skip
        scale_percent = settings.get('scale', 100)
        params = OptimizationParams(quality=quality, lossy=lossy, frame_skip=frame_skip, output_path=params.output_path)

        try:
            if self.cancellation_event.is_set():
                return float('inf'), "", ""

            # Set up temporary output paths
            base_output = os.path.splitext(params.output_path)[0]
            temp_output = f"{base_output}.temp_{batch_id}_{attempt_id}.gif"
            temp_output_optimized = f"{temp_output}_optimized.gif"
            os.makedirs(os.path.dirname(params.output_path), exist_ok=True)

            # Brief status update without replacing previous logs
            self.log(f"Starting attempt {attempt_id}: q={quality}, l={lossy}, skip={frame_skip}")

            # Prepare skipped frames directory
            skip_dir = await self.prepare_frames_with_skip(frames_dir, frame_skip, batch_id, attempt_id)
            if self.cancellation_event.is_set():
                return float('inf'), "", skip_dir
            if not skip_dir or not os.path.isdir(skip_dir):
                raise RuntimeError(f"Skip directory not created or missing: {skip_dir}")
            frames = sorted([f for f in os.listdir(skip_dir) if f.endswith('.png')])
            if not frames:
                raise RuntimeError(f"No frames found in skip directory: {skip_dir}")

            effective_fps = current_fps / frame_skip if frame_skip > 1 else current_fps

            # Determine scaling dimensions from the first frame
            first_frame_path = os.path.join(skip_dir, frames[0])
            from PIL import Image
            with Image.open(first_frame_path) as img:
                width, height = img.size
            scaled_width = max(int(width * scale_percent / 100), 1)
            scaled_height = max(int(height * scale_percent / 100), 1)

            # Get list of all frame files in order rather than using wildcards
            frame_files = sorted(glob.glob(os.path.join(skip_dir, 'frame_*.png')))

            # Setup platform-specific subprocess kwargs
            subprocess_kwargs = {'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE}
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                subprocess_kwargs['startupinfo'] = startupinfo
                # Use CREATE_NO_WINDOW (and SW_HIDE if frozen) to ensure no visible console
                if getattr(sys, 'frozen', False):
                    subprocess_kwargs['creationflags'] = CREATE_NO_WINDOW | SW_HIDE
                else:
                    subprocess_kwargs['creationflags'] = CREATE_NO_WINDOW

            # Build and run gifski command with explicit frame files instead of frame pattern
            gifski_cmd = [
                GIFSKI_PATH,
                '--output', temp_output,
                '--quality', str(quality),
                '--fps', str(effective_fps),
                '--width', str(scaled_width),
                '--height', str(scaled_height),
                '--no-sort',
            ]
            # Add each frame file as a separate argument
            gifski_cmd.extend(frame_files)

            # Start gifski with longer timeout - frame count dependent
            frame_count = len(frame_files)
            # Calculate timeout based on frame count - more frames need more time
            timeout = max(60, min(300, frame_count * 0.5))  # Between 60s and 300s based on frame count

            gifski_proc = await asyncio.create_subprocess_exec(*gifski_cmd, **subprocess_kwargs)

            # Wait for completion with cancellation checks
            while True:
                if self.cancellation_event.is_set():
                    gifski_proc.terminate()
                    await gifski_proc.wait()
                    return float('inf'), "", skip_dir

                # Check if process is done
                try:
                    await asyncio.wait_for(asyncio.shield(gifski_proc.wait()), timeout=0.5)
                    break  # Process completed
                except asyncio.TimeoutError:
                    # Still running, continue waiting
                    await asyncio.sleep(0.1)

                # Check if exceeding total timeout
                if gifski_proc.returncode is None:
                    # Process still running, continue
                    pass

            if not os.path.exists(temp_output):
                raise RuntimeError("GIF generation via gifski failed")

            if self.cancellation_event.is_set():
                return float('inf'), "", skip_dir

            # Determine loop parameter for gifsicle based on user settings
            play_count = settings.get('loop_count', 0)
            loop_param = None
            if play_count == 1:
                loop_param = '--no-loop'
            elif play_count > 1:
                loop_param = f'--loop={play_count - 1}'

            gifsicle_cmd = [
                GIFSICLE_PATH,
                f'--lossy={lossy}',
                '-O3',
                '--careful',
                '--no-warnings',
                '--no-ignore-errors',
                '--resize-method=sample'
            ]
            if loop_param:
                gifsicle_cmd.append(loop_param)
            if settings.get('preserve_animated_alpha', False):
                gifsicle_cmd.extend(['--optimize-transparency', '--no-conserve-memory'])
            gifsicle_cmd.extend(['-i', temp_output, '-o', temp_output_optimized])

            gifsicle_proc = await asyncio.create_subprocess_exec(*gifsicle_cmd, **subprocess_kwargs)

            # Wait for completion with cancellation checks
            while True:
                if self.cancellation_event.is_set():
                    gifsicle_proc.terminate()
                    await gifsicle_proc.wait()
                    return float('inf'), "", skip_dir

                # Check if process is done
                try:
                    await asyncio.wait_for(asyncio.shield(gifsicle_proc.wait()), timeout=0.5)
                    break  # Process completed
                except asyncio.TimeoutError:
                    # Still running, continue waiting
                    await asyncio.sleep(0.1)

            if not os.path.exists(temp_output_optimized):
                raise RuntimeError("GIF optimization via gifsicle failed")

            size = os.path.getsize(temp_output_optimized)
            self.log(f"✓ Attempt {attempt_id} complete: {size / 1024:.1f} KB")
            return size, temp_output_optimized, skip_dir
        except Exception as e:
            self.log(f"✗ Attempt {attempt_id} failed: {str(e)}")
            return float('inf'), "", skip_dir
        finally:
            if temp_output and os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except Exception as e:
                    self.log(f"Error removing temporary file: {e}")

    async def convert_to_gif(self, input_path: str, desired_size: Optional[int]):
        """Main conversion method"""
        frames_dir = None
        temp_parent_dir = None
        temp_files_to_cleanup = set()
        best_result = None
        best_size = float('inf')
        best_batch_results = None
        attempt_counter = 0
        found_acceptable_result = False

        try:
            output_path = os.path.splitext(input_path)[0] + '_optimized.gif'
            target_size_bytes = desired_size * 1024 if desired_size else None

            # Create a parent directory for all temporary files
            parent_dir = os.path.dirname(input_path)
            temp_parent_dir = os.path.join(parent_dir, 'gif_conversion_temp')
            if os.path.exists(temp_parent_dir):
                shutil.rmtree(temp_parent_dir)
            os.makedirs(temp_parent_dir)

            # Create frames directory inside temp parent directory
            frames_dir = os.path.join(temp_parent_dir, 'frames_temp')
            os.makedirs(frames_dir)

            self.log("Starting conversion process...")

            # Check for early cancellation
            if self.cancellation_event.is_set():
                self.log("\nConversion cancelled by user")
                return

            # Initial frame extraction
            is_gif = input_path.lower().endswith('.gif')

            if is_gif:
                self.log("Extracting frames from GIF...")
                frame_count, avg_duration = await self.run_in_executor(
                    self.extract_gif_frames, input_path, frames_dir
                )
                current_fps = round(1000 / avg_duration) if avg_duration > 0 else 15
                self.log(f"✓ Extracted {frame_count} frames at {current_fps} FPS")
            else:
                current_fps = await self.run_in_executor(self.get_video_fps, input_path)
                self.log("Extracting frames from video...")
                await self.run_in_executor(
                    self.extract_frames, input_path, frames_dir, current_fps
                )
                frames = sorted([f for f in os.listdir(frames_dir) if f.endswith('.png')])
                self.log(f"✓ Extracted {len(frames)} frames at {current_fps} FPS")

            # Check for cancellation after frame extraction
            if self.cancellation_event.is_set():
                self.log("\nConversion cancelled by user")
                return

            # Verify frames were extracted
            frames = sorted([f for f in os.listdir(frames_dir) if f.endswith('.png')])
            if not frames:
                raise RuntimeError("No frames were extracted from the input file")

            # Check for transparency
            has_transparency = await self.run_in_executor(self.has_transparency, frames_dir)

            if has_transparency:
                self.log("Detected transparency in frames, processing alpha channel...")
                # Apply transparency mask
                first_frame_path = os.path.join(frames_dir, frames[0])
                success = await self.apply_transparency_mask(frames_dir, first_frame_path)
                if not success:
                    raise RuntimeError("Failed to apply transparency mask")
            else:
                self.log("No transparency detected, skipping alpha channel processing...")

            if desired_size is None:
                # Use maximum quality settings
                self.log("\nConverting with maximum quality settings...")

                # Check for cancellation before max quality conversion
                if self.cancellation_event.is_set():
                    self.log("\nConversion cancelled by user")
                    return

                temp_output = os.path.join(temp_parent_dir, 'max_quality.gif')
                temp_output_optimized = os.path.join(temp_parent_dir, 'max_quality_optimized.gif')

                # Get list of all frame files in order rather than using wildcards
                frame_files = sorted(glob.glob(os.path.join(frames_dir, 'frame_*.png')))

                # Set up subprocess configurations
                subprocess_kwargs = {
                    'stdout': subprocess.PIPE,
                    'stderr': subprocess.PIPE
                }

                if sys.platform == 'win32':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE
                    subprocess_kwargs['startupinfo'] = startupinfo

                    if getattr(sys, 'frozen', False):
                        subprocess_kwargs['creationflags'] = CREATE_NO_WINDOW | SW_HIDE
                    else:
                        subprocess_kwargs['creationflags'] = CREATE_NO_WINDOW
                else:
                    # Ensure executable permissions on macOS for GIFSKI
                    try:
                        os.chmod(GIFSKI_PATH, 0o755)
                    except Exception as e:
                        self.log(f"Warning: Could not set executable permissions on gifski: {str(e)}")

                # Get frame dimensions and apply scaling
                width = height = 0
                with Image.open(os.path.join(frames_dir, frames[0])) as first_frame:
                    width, height = first_frame.size

                # Get scale setting
                settings = OptionsWindow.load_settings()
                scale_percent = settings.get('scale', 100)

                # Calculate scaled dimensions
                scaled_width = max(int(width * scale_percent / 100), 1)
                scaled_height = max(int(height * scale_percent / 100), 1)

                # Build gifski command with explicit frame files instead of pattern
                gifski_cmd = [
                    GIFSKI_PATH,
                    '--output', temp_output,
                    '--quality', '100',
                    '--fps', str(current_fps),
                    '--width', str(scaled_width),
                    '--height', str(scaled_height),
                    '--no-sort',
                ]
                # Add each frame file explicitly
                gifski_cmd.extend(frame_files)

                gifski_proc = await asyncio.create_subprocess_exec(
                    *gifski_cmd,
                    **subprocess_kwargs
                )

                try:
                    await asyncio.wait_for(gifski_proc.wait(), timeout=0.5)
                    while gifski_proc.returncode is None:
                        if self.cancellation_event.is_set():
                            gifski_proc.terminate()
                            await gifski_proc.wait()
                            self.log("\nConversion cancelled by user")
                            return
                        await asyncio.sleep(0.1)
                except asyncio.TimeoutError:
                    if self.cancellation_event.is_set():
                        gifski_proc.terminate()
                        await gifski_proc.wait()
                        self.log("\nConversion cancelled by user")
                        return
                    await gifski_proc.wait()

                # Check for cancellation before optimization
                if self.cancellation_event.is_set():
                    self.log("\nConversion cancelled by user")
                    return

                # Before running gifsicle, ensure executable permissions on macOS for GIFSICLE
                if sys.platform != 'win32':
                    try:
                        os.chmod(GIFSICLE_PATH, 0o755)
                    except Exception as e:
                        self.log(f"Warning: Could not set executable permissions on gifsicle: {str(e)}")

                # Run gifsicle with cancellation check
                settings = OptionsWindow.load_settings()
                play_count = settings.get('loop_count', 0)

                if play_count == 0:
                    loop_param = None
                else:
                    loop_count = play_count - 1
                    loop_param = f'--loop={loop_count}'

                gifsicle_process = await asyncio.create_subprocess_exec(
                    GIFSICLE_PATH,
                    '-O3',
                    '--careful',
                    '--no-warnings',
                    '--no-ignore-errors',
                    '--resize-method=sample',
                    *([] if not loop_param else [loop_param]),
                    '-i', temp_output,
                    '-o', temp_output_optimized,
                    **subprocess_kwargs
                )

                try:
                    await asyncio.wait_for(gifsicle_process.wait(), timeout=0.5)
                    while gifsicle_process.returncode is None:
                        if self.cancellation_event.is_set():
                            gifsicle_process.terminate()
                            await gifsicle_process.wait()
                            self.log("\nConversion cancelled by user")
                            return
                        await asyncio.sleep(0.1)
                except asyncio.TimeoutError:
                    if self.cancellation_event.is_set():
                        gifsicle_process.terminate()
                        await gifsicle_process.wait()
                        self.log("\nConversion cancelled by user")
                        return
                    await gifsicle_process.wait()

                # Save the result
                if os.path.exists(temp_output_optimized):
                    try:
                        self.log("\nFinalizing...")

                        # Check if ImageMagick optimization is enabled
                        settings = OptionsWindow.load_settings()
                        use_imagemagick = settings.get('use_imagemagick', True)

                        if use_imagemagick:
                            self.log("Applying experimental optimization pass...")
                            final_path = await self.apply_imagemagick_optimization(temp_output_optimized,
                                                                                   output_path)
                        else:
                            # Copy the file directly to the output path
                            shutil.copy2(temp_output_optimized, output_path)
                            final_path = output_path

                        if os.path.exists(final_path):
                            final_size = os.path.getsize(final_path)
                            message = f"GIF saved successfully!\nSize: {final_size / 1024:.1f}KB"
                            self.log(f"✓ Conversion complete: {final_size / 1024:.1f}KB")
                            if not hasattr(self, 'suppress_dialogs') or not self.suppress_dialogs:
                                messagebox.showinfo("Success", message)
                        else:
                            raise RuntimeError("Failed to save final GIF")

                    except Exception as e:
                        self.log(f"Error during final optimization: {str(e)}")
                        raise RuntimeError("Failed to create final GIF")
                else:
                    raise RuntimeError("Failed to create initial GIF")

            else:
                # Define optimization batches
                batch_params = [
                    # Batch 1: High quality, minimal frame skip
                    {
                        'qualities': [100, 95, 90],
                        'lossies': [0, 20, 40],
                        'frame_skips': [0, 1]
                    },
                    # Batch 2: Medium quality, moderate frame skip
                    {
                        'qualities': [90, 85, 80],
                        'lossies': [60, 70, 80],
                        'frame_skips': [1, 2]
                    },
                    # Batch 3: Low quality, more aggressive frame skip
                    {
                        'qualities': [80, 80, 80],
                        'lossies': [60, 70, 80],
                        'frame_skips': [2, 3, 4]
                    }
                ]

                self.log("\nStarting optimization process...")
                all_batch_results_under_target = False

                for batch_idx, batch in enumerate(batch_params, 1):
                    if self.cancellation_event.is_set():
                        self.log("\nConversion cancelled by user")
                        return

                    self.log(f"\nTrying optimization batch {batch_idx}/3...")

                    batch_dir = os.path.join(temp_parent_dir, f'batch_{batch_idx}')
                    os.makedirs(batch_dir, exist_ok=True)

                    tasks = []
                    for quality in batch['qualities']:
                        for lossy in batch['lossies']:
                            for frame_skip in batch['frame_skips']:
                                if self.cancellation_event.is_set():
                                    break

                                params = OptimizationParams(
                                    quality=quality,
                                    lossy=lossy,
                                    frame_skip=frame_skip,
                                    output_path=os.path.join(batch_dir, f'attempt_{attempt_counter}')
                                )
                                task = self.try_optimization_params(
                                    frames_dir, params, current_fps, batch_idx, attempt_counter
                                )
                                tasks.append(task)
                                attempt_counter += 1

                    if self.cancellation_event.is_set():
                        break

                    # Use asyncio.as_completed to handle results as they come in
                    pending = [asyncio.create_task(t) for t in tasks]
                    batch_results_under_target = True  # Track if all results in this batch are under target

                    try:
                        for coro in asyncio.as_completed(pending):
                            if self.cancellation_event.is_set():
                                # Cancel all pending tasks
                                for task in pending:
                                    if not task.done():
                                        task.cancel()
                                break

                            try:
                                size, temp_path, skip_dir = await coro
                                if skip_dir and skip_dir != frames_dir:
                                    temp_files_to_cleanup.add(skip_dir)

                                if size != float('inf') and temp_path and os.path.exists(temp_path):
                                    if size <= target_size_bytes:
                                        if best_size == float('inf') or abs(target_size_bytes - size) < abs(
                                                target_size_bytes - best_size):
                                            best_size = size
                                            best_result = temp_path
                                            self.log(f"New best result: {best_size / 1024:.1f}KB")

                                            # If we're very close to target size (within 5%), consider it good enough
                                            if abs(target_size_bytes - size) / target_size_bytes < 0.05:
                                                found_acceptable_result = True
                                                break
                                    else:
                                        batch_results_under_target = False  # At least one result was over target
                            except asyncio.CancelledError:
                                continue
                    except asyncio.CancelledError:
                        pass

                    # Clean up batch directory
                    temp_files_to_cleanup.add(batch_dir)

                    if self.cancellation_event.is_set() or found_acceptable_result:
                        break

                    # If all results in this batch were under target size, skip remaining batches
                    if batch_results_under_target and best_result is not None:
                        self.log("\nAll results in current batch meet size requirement. Skipping remaining batches.")
                        break

                # Save the best result if we found one
                if best_result and os.path.exists(best_result) and not self.cancellation_event.is_set():
                    try:
                        self.log("\nFinalizing...")
                        if os.path.exists(output_path):
                            os.remove(output_path)

                        # Check if ImageMagick optimization is enabled
                        settings = OptionsWindow.load_settings()
                        use_imagemagick = settings.get('use_imagemagick', True)

                        if use_imagemagick:
                            self.log("Applying experimental optimization pass...")
                            final_path = await self.apply_imagemagick_optimization(best_result, output_path)
                        else:
                            shutil.copy2(best_result, output_path)
                            final_path = output_path

                        if os.path.exists(final_path):
                            final_size = os.path.getsize(final_path)
                            if final_size <= target_size_bytes:
                                size_diff_percentage = (target_size_bytes - final_size) / target_size_bytes * 100
                                message = (f"GIF saved successfully!\n"
                                           f"Size: {final_size / 1024:.1f}KB\n"
                                           f"({size_diff_percentage:.1f}% under target)")
                                self.log(f"✓ Conversion complete: {final_size / 1024:.1f}KB")
                            else:
                                message = (f"Warning: GIF size ({final_size / 1024:.1f}KB) "
                                           f"exceeds target ({target_size_bytes / 1024:.1f}KB)")
                                self.log(f"⚠ {message}")
                            if not hasattr(self, 'suppress_dialogs') or not self.suppress_dialogs:
                                messagebox.showinfo("Success", message)
                        else:
                            raise RuntimeError("Failed to save final GIF")
                    except Exception as e:
                        self.log(f"✗ Error saving final result: {str(e)}")
                        if not hasattr(self, 'suppress_dialogs') or not self.suppress_dialogs:
                            messagebox.showerror("Error", f"Failed to save final GIF: {str(e)}")
                        raise RuntimeError(f"Failed to save final GIF: {str(e)}")
                elif not self.cancellation_event.is_set():
                    self.log("✗ No suitable result found under size limit")
                    if not hasattr(self, 'suppress_dialogs') or not self.suppress_dialogs:
                        messagebox.showwarning(
                            "Warning",
                            f"Could not achieve target size of {target_size_bytes / 1024:.1f}KB. "
                            "Try increasing the size limit or allowing more compression."
                        )

        except Exception as e:
            if not self.cancellation_event.is_set():
                self.log(f"✗ Error during conversion: {str(e)}")
                if not hasattr(self, 'suppress_dialogs') or not self.suppress_dialogs:
                    messagebox.showerror("Error", str(e))

        finally:
            # Clean up all temporary files
            try:
                self.log("Cleaning up temporary files...")
                for temp_file in temp_files_to_cleanup:
                    try:
                        if os.path.exists(temp_file):
                            if os.path.isdir(temp_file):
                                shutil.rmtree(temp_file)
                            else:
                                os.remove(temp_file)
                    except Exception as e:
                        self.log(f"Error cleaning up {temp_file}: {e}")

                # Clean up parent temporary directory
                if temp_parent_dir and os.path.exists(temp_parent_dir):
                    try:
                        shutil.rmtree(temp_parent_dir)
                    except Exception as e:
                        self.log(f"Error during final cleanup: {str(e)}")
            finally:
                # Reset conversion state and button appearance
                self.is_converting = False
                self.convert_button.configure(
                    text="Convert",
                    style='Primary.TButton',
                    state='normal'
                )
                # Ensure the changes are applied immediately
                self.update()

    def start_pulse_animation(self):
        """Start the pulsing border animation for the smart panel indicator"""
        self.pulse_animation_active = True
        self.pulse_alpha = 0
        self.pulse_increasing = True
        self.animate_pulse()

    def stop_pulse_animation(self):
        """Stop the pulsing animation"""
        self.pulse_animation_active = False

    def switch_tab(self, tab_name):
        """Switch between tabs in the smart panel"""
        if tab_name == "log":
            self.log_tab_button.configure(style='ActiveTab.TButton')
            self.batch_tab_button.configure(style='Tab.TButton')

            # First hide batch frame if visible
            if hasattr(self, 'batch_frame') and self.batch_frame.winfo_viewable():
                self.batch_frame.grid_remove()

            # Then show log frame
            self.log_frame.grid(row=0, column=0, sticky='nsew')

            # Make sure the smart panel is visible
            self.smart_panel.grid()

        elif tab_name == "batch":
            self.log_tab_button.configure(style='Tab.TButton')
            self.batch_tab_button.configure(style='ActiveTab.TButton')

            # First hide log frame
            self.log_frame.grid_remove()

            # Create batch processing UI if it doesn't exist yet
            if not hasattr(self, 'batch_frame') or not self.batch_frame:
                self.create_batch_processing_ui()

            # Show batch frame
            self.batch_frame.grid(row=0, column=0, sticky='nsew')

            # Make sure the smart panel is visible
            self.smart_panel.grid()

        # Update the window size to fit the content
        self.update_idletasks()

        # Get the root window to resize it
        root = self.winfo_toplevel()
        new_height = root.winfo_reqheight()
        current_width = root.winfo_width()
        root.geometry(f"{current_width}x{new_height}")

    def animate_tab_transition(self, target_frame):
        """Animate the transition between tabs"""
        # Just make sure the frame is visible
        target_frame.grid()

    def animate_pulse(self):
        """Animate the pulsing border"""
        if not self.pulse_animation_active:
            return

        # Calculate new alpha value
        self.pulse_alpha += 10 if self.pulse_increasing else -10
        if self.pulse_alpha > 255:
            self.pulse_alpha = 255
            self.pulse_increasing = False
        elif self.pulse_alpha < 0:
            self.pulse_alpha = 0
            self.pulse_increasing = True

        try:
            # Convert alpha to hex and create a color with blue (#007bff) and variable alpha
            alpha_hex = format(self.pulse_alpha, '02x')
            color = f'#{alpha_hex}007bff'

            # Try to directly set the background color
            self.smart_panel_indicator.configure(background=color)
        except:
            # If that fails, fall back to the base style
            pass

        # Schedule the next frame
        self.after(16, self.animate_pulse)  # 16ms = 60fps


def fix_macos_library_permissions():
    """
    Fix permissions and security attributes for bundled binaries and libraries on macOS.
    This should be called early in the application startup.
    """
    if sys.platform != 'darwin':
        return  # Only needed on macOS

    print("Checking macOS binary permissions...")

    # Get the directory where our application and binaries are located
    if getattr(sys, 'frozen', False):
        # We're running in a PyInstaller bundle
        base_path = os.path.join(sys._MEIPASS, 'bin')
    else:
        # We're running in a normal Python environment
        base_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'bin')

    if not os.path.exists(base_path):
        print(f"Warning: Binary path {base_path} not found")
        return

    # List of binaries to check
    binaries = ['ffmpeg', 'gifski', 'gifsicle', 'magick', 'convert']

    # Find and fix each binary
    for binary in binaries:
        # Check main binary
        binary_path = os.path.join(base_path, binary)
        if os.path.exists(binary_path):
            _fix_binary_permissions(binary_path)

        # Check arm64 variant
        arm64_path = os.path.join(base_path, f"{binary}_arm64")
        if os.path.exists(arm64_path):
            _fix_binary_permissions(arm64_path)

        # Check universal variant
        universal_path = os.path.join(base_path, f"{binary}_universal")
        if os.path.exists(universal_path):
            _fix_binary_permissions(universal_path)

    # Check for lib directory and fix permissions for all dynamic libraries
    lib_path = os.path.join(base_path, 'lib')
    if os.path.exists(lib_path) and os.path.isdir(lib_path):
        print(f"Fixing permissions for libraries in {lib_path}")
        for root, dirs, files in os.walk(lib_path):
            for file in files:
                if file.endswith('.dylib') or file.endswith('.so'):
                    library_path = os.path.join(root, file)
                    _fix_binary_permissions(library_path)
    else:
        print("No lib directory found")


def _fix_binary_permissions(file_path):
    """Fix permissions and remove quarantine for a single binary or library"""
    try:
        # Make sure the file is executable
        os.chmod(file_path, 0o755)
        print(f"Set executable permissions for {os.path.basename(file_path)}")

        # Remove quarantine attribute if present (requires xattr)
        try:
            # Check if file has quarantine attribute
            result = subprocess.run(['xattr', '-l', file_path],
                                    capture_output=True, text=True)

            if 'com.apple.quarantine' in result.stdout:
                print(f"Removing quarantine attribute from {os.path.basename(file_path)}")
                subprocess.run(['xattr', '-d', 'com.apple.quarantine', file_path],
                               capture_output=True)
        except Exception as e:
            print(f"Could not check/remove quarantine attribute: {e}")

    except Exception as e:
        print(f"Error fixing permissions for {file_path}: {e}")


def main():
    """Main application entry point with improved cross-platform support"""
    # Set up high DPI support for macOS and Windows
    try:
        if sys.platform == 'darwin':
            # macOS specific settings
            os.environ['TK_SILENCE_DEPRECATION'] = '1'  # Silence deprecation warnings on macOS
            fix_macos_library_permissions()
            # Attempt to enable high DPI support for Retina displays
            try:
                from tkinter import _tkinter
                _tkinter.TK_ENABLE_HIDPI = 1
            except:
                pass

        elif sys.platform == 'win32':
            # Windows specific settings
            import ctypes
            # Tell Windows to use per-monitor DPI awareness
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
            except:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()  # Fallback to system DPI aware
                except:
                    pass
    except:
        print("Warning: Failed to set up high DPI support")

    # Initialize the tkinter root with tkinterdnd2
    root = TkinterDnD.Tk()
    root.title("GIFLight")
    root.geometry("800x600")
    root.configure(bg='#1a1a1a')

    # Make window responsive
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    # Create error handler for unhandled exceptions to improve stability
    def handle_exception(exc_type, exc_value, exc_traceback):
        import traceback
        print(f"Uncaught exception: {exc_type.__name__}: {exc_value}")
        traceback.print_exception(exc_type, exc_value, exc_traceback)
        # Show error message to user
        try:
            from tkinter import messagebox
            messagebox.showerror("Error",
                                 f"An unexpected error occurred:\n{exc_type.__name__}: {exc_value}\n\n"
                                 "Please report this issue.")
        except:
            pass

    # Set up global exception handler
    sys.excepthook = handle_exception

    # Initialize the main application
    app = ModernGifConverter(root)

    # Start the main event loop
    root.mainloop()


if __name__ == "__main__":
    main()