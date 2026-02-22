This bot is designed to be used with Docker inside Unraid, however it can also be run with Python as-is.

Replace "TOKEN" in bot.py with your Discord bot token.

Bot commands:
!z = 1:1 square, 1440x1440
!zl = 16:9 landscape, 1920x1088
!zp = 9:16 portrait, 1088x1920
!zr = re-roll last prompt with new seed

Models needed for ComfyUI workflows:
diffusion_models\z_image_bf16.safetensors
vae\ae.safetensors
text_encoders\qwen_3_4b.safetensors
