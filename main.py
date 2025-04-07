from flask import Flask, request, jsonify, after_this_request
import os
import logging
import requests
import tempfile
import subprocess
from urllib.parse import urlparse, unquote
import srt
from datetime import timedelta
import ffmpeg

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Default Font directory
#FONT_DIR = os.path.join(os.path.dirname(__file__), 'fonts')
FONTS_DIR = 'C:\\Windows\\Fonts' # Windows default Font directory
#FONTS_DIR = '/usr/share/fonts' # Linux default Font directory

def download_file(url, storage_path):
    """
    Downloads a file from a URL and saves it to a specified path.

    Args:
        url (str): The URL of the file to download.
        storage_path (str): The path to save the downloaded file.

    Returns:
        str: The path to the downloaded file.
    """
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()

        # Sanitize the filename
        parsed_url = urlparse(url)
        filename = os.path.basename(parsed_url.path)
        filename = unquote(filename)
        # Replace problematic characters with underscores
        safe_filename = "".join(c if c.isalnum() or c in ['.', '-', '_'] else '_' for c in filename)
        temp_file_path = os.path.join(storage_path, safe_filename)

        with open(temp_file_path, 'wb') as temp_file:
            for chunk in response.iter_content(chunk_size=8192):
                temp_file.write(chunk)

            logger.info(f"Downloaded file to {temp_file_path}")
            return temp_file_path
    except requests.RequestException as e:
        logger.error(f"Error downloading file from {url}: {str(e)}")
        return None

        # Save the file


def generate_ass_style(options):
    default_font = 'Arial'
    if os.path.exists(FONTS_DIR):
        available_fonts = [f.split('.')[0] for f in os.listdir(FONTS_DIR) if f.lower().endswith(('.ttf', '.otf'))]
        if options.get('font_family', default_font) in available_fonts:
            default_font = options.get('font_family', default_font)
        else:
            logger.warning(f"Font '{options.get('font_family')}' not found, defaulting to '{default_font}'")
    else:
        logger.warning(f"Font directory '{FONTS_DIR}' does not exist. Using default font Arial.")

    font_size = options.get('font_size', 20)
    primary_color = options.get('text_color', '&H00FFFFFF') # default to white
    outline_color = options.get('outline_color', '&H00000000') # default to black
    outline_width = options.get('outline', 0.5) # default outline width
    position = options.get('position', 'bottom') # default to bottom

    alignment = 2 # Bottom center
    if position == 'top':
        alignment = 8 # Top center
    elif position == 'center':
        alignment = 5 # Middle center
    
    # Map 9:16 to a 9 value, and 16:9 to a 1 value
    border_style = 1
    if options.get('ratio')  == '9:16':
        border_style = 3
    
    style_string = f"Style: Default,{default_font},{font_size},{primary_color},&H000000FF,{outline_color},&H00000000,0,0,0,0,100,100,0,0,{border_style},{outline_width},0,{alignment},0,0,10,0"
    return style_string

def timedelta_to_ass_time(td: timedelta) -> srt:
    """ Converts a timedelta object to ASS time format (HH:MM:SS.mmm)."""
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    milliseconds = int(td.microseconds / 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"

def srt_to_ass(srt_file_path, ass_file_path, options):
    """
    Converts an SRT file to an ASS file using FFmpeg.

    Args:
        srt_file_path (str): The path to the SRT file.
        ass_file_path (str): The path to save the converted ASS file.
        options (dict): Additional styling options for the captions.

    Returns:
        bool: True if the conversion was successful, False otherwise.
    """
    
    try:
        with open(srt_file_path, 'r', encoding='utf-8') as srt_file:
            srt_content = srt_file.read()
            subtitles = list(srt.parse(srt_content))

            style_string = generate_ass_style(options)

        with open(ass_file_path, 'w', encoding='utf-8') as ass_file:
            ass_file.write("[Script Info]\n")
            ass_file.write("Title: Subtitles\n")
            ass_file.write("ScriptType: v4.00+\n")
            ass_file.write("\n")
            ass_file.write("[V4+ Styles]\n")
            ass_file.write(
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
            ass_file.write(f"{style_string}\n")  # Use the generated style string
            ass_file.write("\n")
            ass_file.write("[Events]\n")
            ass_file.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

            for sub in subtitles:
                start_time = timedelta_to_ass_time(sub.start)
                end_time = timedelta_to_ass_time(sub.end)
                text = sub.content.replace("\n", "\\N")
                ass_file.write(f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{text}\n")
        return True
    except Exception as e:
        logger.error(f"Error converting SRT to ASS: {e}")
        return False


@app.route('/caption', methods=['POST'])
def caption_video():
    """
    Adds caption to a video using FFmpeg, with customizable styling options.

    Args:
        video_url (str): The URL of the video file.
        srt_url (str): The URL of the SRT file containing the captions.
        options (dict): Additional styling options for the captions.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    required_fields = ['video_url', 'srt_url']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400

    video_url = data['video_url']
    srt_url = data['srt_url']
    options = data.get('options', {})
    
    # Create a temporary directory for storing files
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            video_file_path = download_file(video_url, temp_dir)
            if video_file_path is None:
                return jsonify({'error': 'Failed to download video file'}), 500
            
            srt_file_path = download_file(srt_url, temp_dir)
            if srt_file_path is None:
                return jsonify({'error': 'Failed to download SRT file'}), 500
            
            # Convert SRT to ASS format
            ass_file_path = 'output.ass'
            if not srt_to_ass(srt_file_path, ass_file_path, options):
                return jsonify({'error': 'Failed to convert SRT to ASS'}), 500
            
            # Determine output filename (use a sanitized version of the video filename)
            video_filename = os.path.basename(video_file_path)
            if video_filename is None:
                return jsonify({"error": "Failed to get video filename"}), 500
            video_name_without_ex, video_ext = os.path.splitext(video_filename)
            safe_video_name = "".join(c if c.isalnum() or c in ['.', '-', '_'] else '_' for c in video_name_without_ex)
            output_filename = f"{safe_video_name}_captioned{video_ext}"
            output_path = output_filename

            # Build FFMPEG command
            ffmpeg_cmd = [
                'ffmpeg',
                '-y', # Overwrite output file if exists
                '-i', video_file_path,
                '-vf', f"subtitles={ass_file_path}",
                '-c:a', 'copy', # Copy audio stream
                '-preset', 'fast',
                output_path
            ]

            logger.info(f"FFmpeg command: { ' '.join(ffmpeg_cmd)}")

            # Run FFMPEG
            subprocess.run(ffmpeg_cmd, check=True, capture_output=True) # change from .run()

            # ffmpeg.input(video_file_path).output(
            #     output_path,
            #     vf=ass_file_path,
            #     acodec='copy'
            # ).run()
            # logger.info(f"FFmpeg processing complete")

            # Return the path to the processed video.  In a real application, you'd
            # probably want to upload this to a storage service (like S3) and
            # return the URL.  For this example, we'll just return the local path.
            return jsonify({"output_path": output_path, "output_filename": output_filename}), 200
        
        except subprocess.CalledProcessError as e:
            error_message = str(e.stderr.decode('utf-8'))
            logger.error(f"FFmpeg error: {error_message}")
            return jsonify({"error": f"FFmpeg processing failed: {error_message}"}), 500
        except Exception as e:
            logger.error(f"Error processing request: {e}")
            return jsonify({"error": f"An error occured: {e}"}), 500
        
        
    


if __name__ == '__main__':
    app.run(debug=True)
