# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

StreamCap is a multi-platform live streaming recorder client based on FFmpeg and StreamGet. It supports live streaming recording from 40+ mainstream domestic and international platforms, featuring batch recording, loop monitoring, scheduled monitoring, and automatic transcoding.

## Key Features

- Multi-platform support: Windows/macOS/Web execution
- Loop monitoring: Real-time monitoring of live room status, recording when stream starts
- Scheduled tasks: Check live room status according to set time ranges
- Multiple output formats: Supports ts, flv, mkv, mov, mp4, mp3, m4a formats
- Automatic transcoding: Automatically transcodes to mp4 format after recording
- Message push notifications: Supports live status push notifications

## Architecture Overview

The application is built with the following core components:

1. **Core Application Management** (`app/app_manager.py`): Central App class managing all components
2. **Recording Management** (`app/core/recording/`): Handles live stream monitoring, recording, and management
3. **Platform Handlers** (`app/core/platforms/`): Platform-specific stream data fetchers for 40+ platforms
4. **Media Processing** (`app/core/media/`): FFmpeg command builders for different formats
5. **UI Components** (`app/ui/`): Flet-based desktop/web interface
6. **Configuration Management** (`app/core/config/`): Handles user settings, defaults, and persistence
7. **Process Management** (`app/core/runtime/`): Manages FFmpeg processes and background tasks

## Common Development Tasks

### Running the Application

1. Desktop mode (default):
   ```bash
   python main.py
   ```

2. Web mode:
   ```bash
   python main.py --web
   ```
   Or set `PLATFORM=web` in .env

### Building/Running with Docker

```bash
docker compose up
```

## Testing

Testing is handled through GitHub Actions workflows in `.github/workflows/test.yml`. Use pytest for unit tests and integration tests.

## Code Structure

- `main.py`: Entry point for the application
- `app/`: Main application code
  - `app/app_manager.py`: Core application manager
  - `app/core/`: Core functionality modules
  - `app/ui/`: User interface components
  - `app/scripts/`: Installation and setup scripts
- `config/`: Default configuration files
- `assets/`: Images, icons, fonts
- `bin/`: Binary executables (FFmpeg)
- `requirements.txt`: Desktop dependencies
- `requirements-web.txt`: Web dependencies

## Important Dependencies

- `flet`: Cross-platform UI framework
- `streamget`: Core library for fetching stream data from platforms
- `ffmpeg`: Media processing (automatically installed if missing)

## Configuration Files

- `config/default_settings.json`: Default application settings
- `.env`: Environment variables (copy from .env.example)
- User settings are stored in the config directory as JSON files

## Platform Support

The application supports over 40 platforms including:
- Domestic: Douyin, Kuaishou, Huya, Douyu, Bilibili, Xiaohongshu, YY, etc.
- International: TikTok, Twitch, YouTube, etc.

Each platform has its own handler in `app/core/platforms/platform_handlers/`.