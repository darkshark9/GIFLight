import sys
import tkinter as tk
from tkinter import ttk
import json
import os


class OptionsWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)

        # Window setup
        self.title("GIFLight Options")
        self.geometry("400x400")  # Increased height for tooltips
        self.configure(bg='#1a1a1a')

        # Make window modal
        self.transient(master)
        self.grab_set()

        # Initialize style
        self.style = ttk.Style()
        self.setup_styles()

        # Load saved settings
        self.settings = self.load_settings()
        self.loop_count = tk.StringVar(value=str(self.settings.get('loop_count', 0)))

        # Initialize scale variable with default
        self.scale_value = tk.StringVar(value=str(self.settings.get('scale', 100)))
        self.use_imagemagick = tk.BooleanVar(value=self.settings.get('use_imagemagick', False))

        self.create_widgets()

        # Center the window
        self.center_window()

    def setup_styles(self):
        """Configure custom styles for the options window"""
        self.style.configure('Options.TFrame',
                             background='#1a1a1a',
                             padding=20)

        self.style.configure('Options.TCheckbutton',
                             background='#1a1a1a',
                             foreground='white',
                             font=('Segoe UI', 10))

        self.style.configure('Options.TLabel',  # Add label style
                             background='#1a1a1a',
                             foreground='white',
                             font=('Segoe UI', 10))

        self.style.configure('Options.TEntry',  # Add entry style
                             fieldbackground='#2d2d2d',
                             foreground='white')

        self.style.configure('Options.TButton',
                             font=('Segoe UI', 10))

    def create_widgets(self):
        """Create and arrange all widgets in the options window"""
        # Main container
        main_frame = ttk.Frame(self, style='Options.TFrame')
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Variables for checkboxes
        self.lock_quality = tk.BooleanVar(value=self.settings.get('lock_quality', False))
        self.lock_lossy = tk.BooleanVar(value=self.settings.get('lock_lossy', False))
        self.lock_frame_skip = tk.BooleanVar(value=self.settings.get('lock_frame_skip', False))
        self.preserve_animated_alpha = tk.BooleanVar(value=self.settings.get('preserve_animated_alpha', False))

        # Option groups with tooltips
        self.add_option_group(main_frame,
                            "Lock Quality at Maximum",
                            "Disables quality compression entirely",
                            self.lock_quality)

        self.add_option_group(main_frame,
                            "Disable Diffusion",
                            "Disables grain diffusion",
                            self.lock_lossy)

        self.add_option_group(main_frame,
                            "Do not lower frame rate",
                            "Maintains original frame rate",
                            self.lock_frame_skip)

        self.add_option_group(main_frame,
                            "Preserve animated transparency",
                            "Keeps per-frame transparency but may increase file size",
                            self.preserve_animated_alpha)

        self.add_option_group(main_frame,
                              "Use experimental compression",
                              "New lossy compressions method, may further reduce file size up to 50%",
                              self.use_imagemagick)

        self.add_loop_count_group(main_frame)

        # Add scale input group before the spacer
        self.add_scale_group(main_frame)

        # Create a spacer frame to push buttons to bottom
        spacer = ttk.Frame(main_frame, style='Options.TFrame')
        spacer.pack(expand=True, fill=tk.BOTH)

        # Buttons frame at the bottom
        button_frame = ttk.Frame(main_frame, style='Options.TFrame')
        button_frame.pack(fill=tk.X, pady=(0, 10))

        # Center container for buttons
        center_frame = ttk.Frame(button_frame, style='Options.TFrame')
        center_frame.pack(expand=True, anchor='center')

        # Save and Cancel buttons in center frame
        ttk.Button(center_frame,
                   text="Save",
                   command=self.save_and_close,
                   style='Options.TButton').pack(side=tk.LEFT, padx=5)

        ttk.Button(center_frame,
                   text="Cancel",
                   command=self.destroy,
                   style='Options.TButton').pack(side=tk.LEFT, padx=5)

    def add_option_group(self, parent, checkbox_text, tooltip_text, variable):
        """Helper method to create a checkbox with its tooltip"""
        # Container for the option group
        group_frame = ttk.Frame(parent, style='Options.TFrame')
        group_frame.pack(fill=tk.X, pady=(0, 5))

        # Checkbox
        checkbox = ttk.Checkbutton(group_frame,
                                   text=checkbox_text,
                                   variable=variable,
                                   style='Options.TCheckbutton')
        checkbox.pack(anchor='w')

        # Create tooltip label with explicit dark theme configuration
        tooltip = tk.Label(group_frame,
                           text=tooltip_text,
                           bg='#1a1a1a',  # Explicit background color
                           fg='#555555',  # Explicit foreground color
                           font=('Segoe UI', 8, 'italic'),
                           padx=20,
                           pady=0,
                           anchor='w',
                           justify='left')

        # Force the color update after creation
        tooltip.configure(foreground='#555555')
        tooltip.pack(fill=tk.X)

    def center_window(self):
        """Center the window on the screen"""
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f'{width}x{height}+{x}+{y}')

    def add_loop_count_group(self, parent):
        """Add play count input field with label and validation"""
        # Container for the loop count group
        loop_frame = ttk.Frame(parent, style='Options.TFrame')
        loop_frame.pack(fill=tk.X, pady=(0, 5))

        # Label - Updated to clarify it's about plays, not loops
        loop_label = ttk.Label(loop_frame,
                             text="Play Count (0 for infinite):",
                             style='Options.TLabel')
        loop_label.pack(anchor='w')

        def validate_loop_count(value):
            if value == "":  # Allow empty string for backspace
                return True
            try:
                if len(value) > 3:  # Don't allow more than 3 digits
                    return False
                num = int(value)
                return num >= 0  # Only allow non-negative numbers
            except ValueError:
                return False

        # Input field with validation
        vcmd = (self.register(validate_loop_count), '%P')
        loop_entry = ttk.Entry(loop_frame,
                             textvariable=self.loop_count,
                             validate='key',
                             validatecommand=vcmd,
                             width=10,
                             style='Options.TEntry')
        loop_entry.pack(anchor='w', padx=20)

        # Tooltip - Updated to be more precise about behavior
        tooltip = tk.Label(loop_frame,
                         text="Total number of times the GIF will play (0 = play forever)",
                         bg='#1a1a1a',
                         fg='#555555',
                         font=('Segoe UI', 8, 'italic'),
                         padx=20,
                         pady=0,
                         anchor='w',
                         justify='left')
        tooltip.pack(fill=tk.X)


    def add_scale_group(self, parent):
        """Add scale input field with label and validation"""
        # Container for the scale group
        scale_frame = ttk.Frame(parent, style='Options.TFrame')
        scale_frame.pack(fill=tk.X, pady=(0, 5))

        # Label
        scale_label = ttk.Label(scale_frame,
                                text="Output Scale (%):",
                                style='Options.TLabel')
        scale_label.pack(anchor='w')

        # Create a more permissive validation
        def validate_scale(value):
            if value == "":  # Allow empty string for backspace
                return True
            try:
                if len(value) > 3:  # Don't allow more than 3 digits
                    return False
                num = int(value)
                return 0 <= num <= 100  # More permissive range check
            except ValueError:
                return False

        # Input field with validation
        vcmd = (self.register(validate_scale), '%P')
        scale_entry = ttk.Entry(scale_frame,
                                textvariable=self.scale_value,
                                validate='key',
                                validatecommand=vcmd,
                                width=10,
                                style='Options.TEntry')
        scale_entry.pack(anchor='w', padx=20)

        # Tooltip
        tooltip = tk.Label(scale_frame,
                           text="Scale the output GIF dimensions (25-100)",
                           bg='#1a1a1a',
                           fg='#555555',
                           font=('Segoe UI', 8, 'italic'),
                           padx=20,
                           pady=0,
                           anchor='w',
                           justify='left')
        tooltip.pack(fill=tk.X)

    def save_and_close(self):
        """Save the current settings and close the window"""
        try:
            # More permissive scale validation on save
            scale = int(self.scale_value.get() or 100)  # Default to 100 if empty
            scale = max(25, min(100, scale))  # Clamp between 25 and 100
        except (ValueError, TypeError):
            scale = 100

        try:
            loop_count = int(self.loop_count.get() or 0)  # Default to 0 if empty
            loop_count = max(0, loop_count)  # Ensure non-negative
        except (ValueError, TypeError):
            loop_count = 0

        settings = {
            'lock_quality': self.lock_quality.get(),
            'lock_lossy': self.lock_lossy.get(),
            'lock_frame_skip': self.lock_frame_skip.get(),
            'preserve_animated_alpha': self.preserve_animated_alpha.get(),
            'scale': scale,
            'loop_count': loop_count,
            'use_imagemagick': self.use_imagemagick.get()
        }
        self.save_settings(settings)
        self.destroy()

    @staticmethod
    def get_settings_path():
        """Get the path to the settings file"""
        if getattr(sys, 'frozen', False):
            # Running in a bundle
            base_path = os.path.dirname(sys.executable)
        else:
            # Running in a normal Python environment
            base_path = os.path.dirname(os.path.abspath(__file__))

        return os.path.join(base_path, 'giflight_settings.json')

    @classmethod
    def load_settings(cls):
        """Load settings from file"""
        try:
            settings_path = cls.get_settings_path()
            if os.path.exists(settings_path):
                with open(settings_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading settings: {e}")
        return {}

    @classmethod
    def save_settings(cls, settings):
        """Save settings to file"""
        try:
            settings_path = cls.get_settings_path()
            with open(settings_path, 'w') as f:
                json.dump(settings, f)
        except Exception as e:
            print(f"Error saving settings: {e}")