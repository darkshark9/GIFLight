import asyncio
import concurrent.futures
import os
import shutil
import sys
import tempfile
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk, messagebox
from typing import Optional, Tuple

import ttkbootstrap as ttk
from PIL import Image, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD

# Add Windows-specific import
if sys.platform == 'win32':
    import subprocess

    CREATE_NO_WINDOW = 0x08000000
else:
    CREATE_NO_WINDOW = 0


def get_binary_path(binary_name: str) -> str:
    """Get the correct path to a binary with improved security practices"""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(os.path.dirname(__file__))
        base_path = os.path.join(base_path, 'bin')

    # Whitelist specific binaries
    allowed_binaries = {'ffmpeg', 'gifski', 'gifsicle', 'ffprobe'}  # Added ffprobe
    if not binary_name.lower().split('.')[0] in allowed_binaries:
        raise ValueError(f"Invalid binary name: {binary_name}")

    if sys.platform == 'win32':
        binary_name = f"{binary_name}.exe"

    binary_path = os.path.join(base_path, binary_name)

    # Verify file exists and is in the expected directory
    if not os.path.isfile(binary_path):
        raise FileNotFoundError(f"Required component not found: {binary_name}")

    # Verify binary is in the correct directory
    if not os.path.normpath(binary_path).startswith(os.path.normpath(base_path)):
        raise ValueError("Invalid binary path")

    return binary_path


# Update all the paths using the same function
FFMPEG_PATH = get_binary_path('ffmpeg')

GIFSKI_PATH = get_binary_path('gifski')
GIFSICLE_PATH = get_binary_path('gifsicle')


@dataclass
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
            file_path = event.data
            file_path = file_path.strip('{}').strip('"\'')

            if self.validate_file(file_path):
                print(f"Valid file dropped: {file_path}")
                converter = self.find_converter(self)
                if converter:
                    converter.set_file(file_path)
                else:
                    print("Could not find ModernGifConverter instance")
            else:
                print(f"Invalid file type: {file_path}")

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
        self.style = ttk.Style("darkly")
        self.setup_styles()
        self.create_widgets()

    @staticmethod
    def run_subprocess_hidden(command, **kwargs):
        """Run subprocess with improved security and proper output handling"""
        if not isinstance(command, list) or not command:
            raise ValueError("Invalid command format")

        binary_name = os.path.basename(command[0]).lower()
        allowed_binaries = {'ffmpeg.exe', 'gifski.exe', 'gifsicle.exe', 'ffprobe.exe'}
        if binary_name not in allowed_binaries:
            raise ValueError(f"Unauthorized binary: {binary_name}")

        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs['startupinfo'] = startupinfo
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

        # Handle ffprobe differently since we need its output
        if binary_name == 'ffprobe.exe':
            # Always set stdout and stderr for ffprobe
            kwargs['stdout'] = subprocess.PIPE
            kwargs['stderr'] = subprocess.PIPE
            return subprocess.run(command, **kwargs)

        # Create a temporary directory for other commands if needed
        temp_dir = None
        try:
            if '-o' in command or '--output' in command:
                temp_dir = tempfile.mkdtemp(prefix='giflight_')
                output_idx = command.index('-o' if '-o' in command else '--output') + 1
                if output_idx < len(command):
                    original_output = command[output_idx]
                    temp_output = os.path.join(temp_dir, os.path.basename(original_output))
                    command[output_idx] = temp_output

            result = subprocess.run(command, **kwargs)

            if temp_dir and 'temp_output' in locals() and os.path.exists(temp_output):
                shutil.move(temp_output, original_output)

            return result

        finally:
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    print(f"Error cleaning up temporary directory: {str(e)}")

    def setup_styles(self):
        """Configure custom styles for widgets"""
        # Main background color
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
                             justify='center')  # Add justify='center'

        # Hover state style also needs the centering
        self.style.configure('Custom.Hover.TLabel',
                             background='#3d3d3d',
                             foreground='#ffffff',
                             font=('Segoe UI', 12, 'bold'),
                             borderwidth=2,
                             relief='solid',
                             padding=20,
                             anchor='center',
                             justify='center')  # Add justify='center'

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
                             font=('Segoe UI', 10, 'bold'))

        self.style.configure('TEntry',
                             fieldbackground='#2d2d2d',
                             foreground='white',
                             insertcolor='white')

        self.style.configure('TProgressbar',
                             background='#007bff',
                             troughcolor='#2d2d2d',
                             borderwidth=0,
                             thickness=10)

    def create_widgets(self):
        """Create and arrange all GUI widgets"""
        self.grid(sticky='nsew', padx=20, pady=20)
        self.grid_columnconfigure(0, weight=1)

        # Logo frame
        logo_frame = ttk.Frame(self)
        logo_frame.grid(row=0, column=0, pady=(0, 20))

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
                    frame = frame.resize((400, 158), Image.Resampling.LANCZOS)
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
        content_frame = ttk.Frame(self, style='TFrame')
        content_frame.grid(row=1, column=0, sticky='nsew')
        content_frame.grid_columnconfigure(0, weight=1)

        # Container frame for drop zone with fixed size and padding
        drop_container = ttk.Frame(content_frame, style='DropZone.TFrame')
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
            content_frame,
            text="No file selected",
            font=('Segoe UI', 10),
            foreground='#888888'
        )
        self.file_label.grid(row=1, column=0, pady=(0, 10))

        # Size input frame with centering
        size_frame = ttk.Frame(content_frame)
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

        # Convert button
        self.convert_button = ttk.Button(
            content_frame,
            text="Convert",
            style='Primary.TButton',
            command=self.start_conversion
        )
        self.convert_button.grid(row=3, column=0, pady=(0, 20))

        # Log frame setup
        self.log_frame = ttk.Frame(content_frame)
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

        self.log_frame.grid_columnconfigure(0, weight=1)
        self.log_text.grid(row=0, column=0, sticky='nsew', padx=(0, 2))
        scrollbar.grid(row=0, column=1, sticky='ns')

        # Initially hide log frame
        self.log_frame.grid_remove()

        # Store the selected file path
        self.selected_file: Optional[str] = None

    def animate_logo(self):
        """Animate the logo GIF"""
        if hasattr(self, 'gif_frames') and self.gif_frames:
            # Update the label with the current frame
            self.logo_label.configure(image=self.gif_frames[self.current_frame])

            # Move to next frame
            self.current_frame = (self.current_frame + 1) % len(self.gif_frames)

            # Schedule the next frame using the correct duration
            duration = self.frame_durations[self.current_frame]
            self.after(duration, self.animate_logo)

    def set_file(self, file_path: str):
        """Set the selected file and update the UI"""
        try:
            self.selected_file = file_path
            filename = os.path.basename(file_path)

            # Print for debugging
            print(f"Setting file: {filename}")

            self.file_label.configure(
                text=f"Selected: {filename}",
                foreground='#995FB6'
            )

            # Update the UI immediately
            self.update()

            # Print confirmation
            print(f"File set successfully: {self.selected_file}")

        except Exception as e:
            print(f"Error setting file: {str(e)}")
            messagebox.showerror("Error", f"Failed to set file: {str(e)}")

    def show_log(self):
        """Show the log area"""
        # Store current width before showing log
        current_width = self.master.winfo_width()

        # Show the log frame
        self.log_frame.grid(row=4, column=0, sticky='nsew', pady=(0, 20))

        # Update the window to fit height while maintaining width
        self.master.update_idletasks()  # Let the window process the new widget
        new_height = self.master.winfo_reqheight()  # Get required height

        # Set new geometry maintaining width but allowing height to adjust
        self.master.geometry(f"{current_width}x{new_height}")

    def log(self, message: str, replace_last: bool = False):
        """Add message to log area"""
        if replace_last:
            # Delete last line
            self.log_text.delete("end-2c linestart", "end-1c")
        self.log_text.insert('end', message + '\n')
        self.log_text.see('end')
        self.update()

    def check_dependencies(self):
        missing = []
        if not os.path.exists(FFMPEG_PATH):
            missing.append("FFmpeg")
        if not os.path.exists(GIFSKI_PATH):
            missing.append("Gifski")
        if not os.path.exists(GIFSICLE_PATH):
            missing.append("Gifsicle")

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

        try:
            desired_size = int(self.size_entry.get())
            if desired_size <= 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Error", "Please enter a valid size in KB.")
            return

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

    async def run_subprocess(self, command):
        if sys.platform == 'win32':
            # Create process with hidden window
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                creationflags=CREATE_NO_WINDOW
            )
        else:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"Process failed: {stderr.decode()}")
        return stdout.decode()

    # Update the extract_frames method
    def extract_frames(self, video_path, frames_dir, fps):
        """Extract frames from video using FFmpeg"""
        ffmpeg_command = [
            FFMPEG_PATH,
            '-i', video_path,
            '-vf', f'fps={fps}',
            '-pix_fmt', 'rgba',
            os.path.join(frames_dir, 'frame_%04d.png')
        ]
        result = self.run_subprocess_hidden(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode != 0:
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
                pass

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

    def get_video_fps(self, video_path):
        """Get the FPS of the input video using FFmpeg"""
        try:
            ffmpeg_command = [
                FFMPEG_PATH,
                '-i', video_path,
                '-hide_banner'  # Reduces unnecessary output
            ]

            # FFmpeg prints to stderr for input information
            kwargs = {'stderr': subprocess.PIPE, 'text': True}
            result = self.run_subprocess_hidden(ffmpeg_command, **kwargs)

            # FFmpeg prints stream information to stderr
            output = result.stderr

            # Look for fps information in the output
            import re
            fps_matches = re.findall(r'(\d+(?:\.\d+)?)\s*fps', output)
            tb_matches = re.findall(r'tb\(r\):\s*(\d+)/\s*(\d+)', output)

            if fps_matches:
                fps = float(fps_matches[0])
                self.log(f"Found FPS directly: {fps}")
                return round(fps)
            elif tb_matches:
                num, den = map(int, tb_matches[0])
                fps = round(num / den)
                self.log(f"Calculated FPS from timebase: {fps}")
                return fps if 1 <= fps <= 120 else 15
            else:
                self.log("Could not detect FPS, using default")
                return 15

        except Exception as e:
            self.log(f"Error detecting FPS: {str(e)}")
            return 15

    def apply_transparency_mask(self, frames_dir, first_frame_path):
        """Apply transparency mask from first frame to all frames"""
        try:
            first_frame = Image.open(first_frame_path)
            if first_frame.mode != 'RGBA':
                first_frame = first_frame.convert('RGBA')

            alpha_mask = first_frame.split()[3]

            frames = sorted([f for f in os.listdir(frames_dir) if f.endswith('.png')])
            total_frames = len(frames)

            self.log(f"Applying transparency mask to {total_frames} frames...")

            for idx, frame_file in enumerate(frames[1:], 1):
                if idx % 10 == 0:  # Update progress every 10 frames
                    self.log(f"Processing frames: {idx}/{total_frames}", replace_last=True)

                frame_path = os.path.join(frames_dir, frame_file)
                frame = Image.open(frame_path)
                if frame.mode != 'RGBA':
                    frame = frame.convert('RGBA')

                r, g, b, _ = frame.split()
                new_frame = Image.merge('RGBA', (r, g, b, alpha_mask))
                new_frame.save(frame_path, 'PNG')

            self.log(f"✓ Transparency processing complete")
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

    async def try_optimization_params(self, frames_dir: str, params: OptimizationParams,
                                      current_fps: float, batch_id: int, attempt_id: int) -> Tuple[int, str, str]:
        """Try a single optimization configuration and return the resulting file size"""
        skip_dir = None
        temp_output = None
        temp_output_optimized = None

        try:
            temp_base = os.path.normpath(params.output_path)
            temp_output = f"{temp_base}.temp_{batch_id}_{attempt_id}"
            temp_output_optimized = f"{temp_output}_optimized"

            os.makedirs(os.path.dirname(temp_output), exist_ok=True)

            skip_dir = await self.prepare_frames_with_skip(frames_dir, params.frame_skip, batch_id, attempt_id)
            working_dir = os.path.normpath(skip_dir)

            frames = sorted([f for f in os.listdir(working_dir) if f.endswith('.png')])
            if not frames:
                raise RuntimeError("No frames found in working directory")

            effective_fps = current_fps / params.frame_skip if params.frame_skip > 1 else current_fps

            frame_pattern = os.path.normpath(os.path.join(working_dir, 'frame_*.png'))

            self.log(
                f"Attempt {attempt_id}: quality={params.quality}, diffusion strength={params.lossy}, skip={params.frame_skip}")

            await self.run_subprocess([
                GIFSKI_PATH,
                '--output', temp_output,
                '--quality', str(params.quality),
                '--fps', str(effective_fps),
                '--no-sort',
                frame_pattern
            ])

            if not os.path.exists(temp_output):
                raise RuntimeError("Conversion failed")

            await self.run_subprocess([
                GIFSICLE_PATH,
                '--lossy=' + str(params.lossy),
                '-O3',
                '--careful',
                '--no-warnings',
                '--no-ignore-errors',
                '-i', temp_output,
                '-o', temp_output_optimized
            ])

            if not os.path.exists(temp_output_optimized):
                raise RuntimeError("Optimization failed")

            size = os.path.getsize(temp_output_optimized)
            self.log(f"✓ Attempt {attempt_id} complete: {size / 1024:.1f}KB")
            return size, temp_output_optimized, skip_dir

        except Exception as e:
            self.log(f"✗ Attempt {attempt_id} failed: {str(e)}")
            return float('inf'), "", skip_dir

        finally:
            if temp_output and os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except Exception as e:
                    self.log(f"Error removing temporary file: {str(e)}")

    async def convert_to_gif(self, input_path: str, desired_size: int):
        """Main conversion method"""
        frames_dir = None
        temp_parent_dir = None
        temp_files_to_cleanup = set()
        best_result = None
        best_size = float('inf')
        attempt_counter = 0
        found_acceptable_result = False

        try:
            output_path = os.path.splitext(input_path)[0] + '_optimized.gif'
            target_size_bytes = desired_size * 1024

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

            # Verify frames were extracted
            frames = sorted([f for f in os.listdir(frames_dir) if f.endswith('.png')])
            if not frames:
                raise RuntimeError("No frames were extracted from the input file")

            # Apply transparency mask
            first_frame_path = os.path.join(frames_dir, frames[0])
            success = await self.run_in_executor(
                self.apply_transparency_mask, frames_dir, first_frame_path
            )
            if not success:
                raise RuntimeError("Failed to apply transparency mask")

            # Define optimization batches
            batch_params = [
                # Batch 1: High quality, minimal frame skip
                {
                    'qualities': [100, 95, 90],
                    'lossies': [20, 40, 60],
                    'frame_skips': [0, 1]
                },
                # Batch 2: Medium quality, moderate frame skip
                {
                    'qualities': [90, 85, 80],
                    'lossies': [60, 80, 100],
                    'frame_skips': [1, 2]
                },
                # Batch 3: Low quality, more aggressive frame skip
                {
                    'qualities': [75, 75, 75],
                    'lossies': [60, 100, 120],
                    'frame_skips': [2, 3, 4]
                }
            ]

            self.log("\nStarting optimization process...")

            for batch_idx, batch in enumerate(batch_params, 1):
                if found_acceptable_result:
                    break

                self.log(f"\nTrying optimization batch {batch_idx}/3...")

                batch_dir = os.path.join(temp_parent_dir, f'batch_{batch_idx}')
                os.makedirs(batch_dir, exist_ok=True)

                tasks = []
                for quality in batch['qualities']:
                    for lossy in batch['lossies']:
                        for frame_skip in batch['frame_skips']:
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

                results = await asyncio.gather(*tasks)

                # Process all results before cleaning up
                valid_results = []
                all_under_target = True  # New flag to track if all results are under target

                for size, temp_path, skip_dir in results:
                    # Check if any result is over target size
                    if size != float('inf') and size > target_size_bytes:
                        all_under_target = False

                    # Only consider valid results
                    if size != float('inf') and size <= target_size_bytes and temp_path and os.path.exists(temp_path):
                        valid_results.append((size, temp_path))
                    if skip_dir and skip_dir != frames_dir:
                        temp_files_to_cleanup.add(skip_dir)

                # If we have valid results and all attempts were under target size,
                # choose the closest one and end processing
                if valid_results and all_under_target:
                    # Sort by closest to target size (but still under)
                    valid_results.sort(key=lambda x: target_size_bytes - x[0])
                    best_size = valid_results[0][0]
                    best_result = valid_results[0][1]
                    self.log(f"\n✓ All attempts in batch {batch_idx} are under target size.")
                    self.log(f"Selecting best result: {best_size / 1024:.1f}KB")
                    found_acceptable_result = True
                    break

                # If we didn't find all results under target, continue with normal processing
                valid_results.sort(key=lambda x: target_size_bytes - x[0])

                for size, temp_path in valid_results:
                    # Calculate how close we are to target size as a percentage
                    size_diff_percentage = (target_size_bytes - size) / target_size_bytes * 100

                    # Update best result if this is better than previous best (and under target size)
                    if size <= target_size_bytes and (best_size == float('inf') or
                                                      abs(target_size_bytes - size) < abs(
                                target_size_bytes - best_size)):
                        old_best = best_result
                        best_result = temp_path
                        best_size = size
                        self.log(f"New best result: {size / 1024:.1f}KB")

                        # Clean up old best result
                        if old_best and old_best != temp_path and os.path.exists(old_best):
                            temp_files_to_cleanup.add(old_best)

                    # Check if result is within acceptable range (within 5% of target, but never over)
                    if size <= target_size_bytes and size_diff_percentage <= 5:
                        found_acceptable_result = True
                        self.log(f"✓ Found optimal result: {size / 1024:.1f}KB")
                        break

                # Clean up batch directory after processing results
                temp_files_to_cleanup.add(batch_dir)

            # Save the best result if we found one
            if best_result and os.path.exists(best_result):
                try:
                    self.log("\nFinalizing...")
                    # Remove existing output file if it exists
                    if os.path.exists(output_path):
                        os.remove(output_path)

                    shutil.copy2(best_result, output_path)

                    if os.path.exists(output_path):
                        final_size = os.path.getsize(output_path)

                        if final_size <= target_size_bytes:
                            size_diff_percentage = (target_size_bytes - final_size) / target_size_bytes * 100
                            message = (f"GIF saved successfully!\n"
                                       f"Size: {final_size / 1024:.1f}KB\n"
                                       f"({size_diff_percentage:.1f}% under target)")
                            self.log(f"✓ Conversion complete: {final_size / 1024:.1f}KB")
                        else:
                            # This shouldn't happen with our new logic, but just in case
                            message = (f"Warning: GIF size ({final_size / 1024:.1f}KB) "
                                       f"exceeds target ({target_size_bytes / 1024:.1f}KB)")
                            self.log(f"⚠ {message}")

                        messagebox.showinfo("Success", message)
                    else:
                        raise RuntimeError("Failed to save final GIF")
                except Exception as e:
                    self.log(f"✗ Error saving final result: {str(e)}")
                    messagebox.showerror("Error", f"Failed to save final GIF: {str(e)}")
            else:
                self.log("✗ No suitable result found under size limit")
                messagebox.showwarning("Warning",
                                       f"Could not achieve target size of {target_size_bytes / 1024:.1f}KB. "
                                       "Try increasing the size limit or allowing more compression.")

        except Exception as e:
            self.log(f"✗ Error during conversion: {str(e)}")
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
                        self.log(f"Error cleaning up {temp_file}: {str(e)}")

                # Clean up parent temporary directory
                if temp_parent_dir and os.path.exists(temp_parent_dir):
                    try:
                        shutil.rmtree(temp_parent_dir)
                    except Exception as e:
                        self.log(f"Error during final cleanup: {str(e)}")
            finally:
                self.is_converting = False
                self.convert_button.configure(state='normal')


def main():
    root = TkinterDnD.Tk()
    root.title("GIFLight")
    root.geometry("800x600")
    root.configure(bg='#1a1a1a')

    # Make window responsive
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    app = ModernGifConverter(root)
    root.mainloop()


if __name__ == "__main__":
    main()
