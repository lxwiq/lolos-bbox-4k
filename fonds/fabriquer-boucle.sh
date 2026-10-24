#!/bin/bash
# usage: make_loop.sh SRC T0 L OUT FPS RETIME [CROPEXPR] [XFADE] [CRF] [MAXRATE]
#  SRC: source video, T0: start (s, in source time), L: output length (s)
#  FPS: output fps (25|30); RETIME: "retime" => re-stamp every frame at FPS (speed change), "fps" => fps filter (drop/dup)
#  CROPEXPR: crop x:y expression after scaling to cover 1920x1080 (default centered)
set -e
SRC="$1"; T0="$2"; L="$3"; OUT="$4"; FPS="$5"; MODE="$6"; CROPXY="${7:-(in_w-1920)/2:(in_h-1080)/2}"; X="${8:-1.5}"; CRF="${9:-23}"; MAXR="${10:-6M}"
# source duration needed (in source seconds); for retime, account for speed factor
SRCFPS=${EFFFPS:-$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$SRC")}
if [ "$MODE" = "retime" ]; then
  # number of output frames needed = (L+X)*FPS ; read that many source frames
  NF=$(python3 -I -c "print(int(round(($L+$X)*$FPS))+2)")
  SRCDUR=$(python3 -I -c "from fractions import Fraction as F; print(float($NF/F('$SRCFPS'))+0.5)")
  TIMING="setpts=N/($FPS*TB)"
else
  SRCDUR=$(python3 -I -c "print($L+$X+0.5)")
  TIMING="fps=$FPS,setpts=PTS-STARTPTS"
fi
XS=$X; OFF=$(python3 -I -c "print($L-$X)"); LX=$(python3 -I -c "print($L+$X)")
FC="[0:v]${PRE}scale=1920:1080:force_original_aspect_ratio=increase:flags=lanczos,crop=1920:1080:$CROPXY,setsar=1,format=yuv420p,$TIMING,trim=duration=$LX,setpts=PTS-STARTPTS,split[s1][s2];\
[s1]trim=start=$XS:duration=$L,setpts=PTS-STARTPTS[main];\
[s2]trim=start=0:duration=$XS,setpts=PTS-STARTPTS[head];\
[main][head]xfade=transition=fade:duration=$XS:offset=$OFF,format=yuv420p[v]"
ffmpeg -nostdin -hide_banner -loglevel error -y -ss "$T0" -t "$SRCDUR" -i "$SRC" -filter_complex "$FC" -map "[v]" -an -sn -dn \
  -c:v libx264 -preset slow -profile:v high -level:v 4.0 -pix_fmt yuv420p -crf "$CRF" -maxrate "$MAXR" -bufsize 12M \
  -r "$FPS" -g $((FPS*2)) -color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv \
  -map_metadata -1 -movflags +faststart "$OUT"
ffprobe -v error -show_entries format=duration,size:stream=codec_name,codec_type,profile,level,width,height,pix_fmt,r_frame_rate -of compact "$OUT"
