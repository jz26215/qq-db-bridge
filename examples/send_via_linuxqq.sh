#!/bin/sh
# 最小发送示例：在 Xvfb 里驱动 Linux QQ，用剪贴板粘贴中文后回车。
#
# 用法：
#   DISPLAY=:98 ./send_via_linuxqq.sh "你好" "群名或好友名"
#
# 可调坐标（不同分辨率/皮肤要改）：
#   INPUT_X / INPUT_Y      输入框中心
#   SEARCH_X / SEARCH_Y    搜索框中心
#   PANE_X / PANE_Y        聊天空白处（用于取消焦点）
#
# 注意：这只是"能发出去"的最小版本，正式实现必须再加"回读数据库确认"。

set -eu

TEXT="${1:?需要发送内容}"
CHAT="${2:-}"

DISPLAY="${DISPLAY:-:98}"
export DISPLAY

INPUT_X="${INPUT_X:-560}"; INPUT_Y="${INPUT_Y:-668}"
SEARCH_X="${SEARCH_X:-386}"; SEARCH_Y="${SEARCH_Y:-246}"
PANE_X="${PANE_X:-300}";   PANE_Y="${PANE_Y:-560}"

TMP_DIR="$(mktemp -d)"
TEXT_FILE="$TMP_DIR/msg.txt"
CHAT_FILE="$TMP_DIR/chat.txt"

printf '%s' "$TEXT" > "$TEXT_FILE"

defocus() { xdotool mousemove "$PANE_X" "$PANE_Y" click 1; sleep 0.3; }

# 1) 切会话（按名字搜索 + 回车）
switch_chat() {
  [ -n "$CHAT" ] || return 0
  printf '%s' "$CHAT" > "$CHAT_FILE"
  xdotool mousemove "$SEARCH_X" "$SEARCH_Y" click 1
  sleep 0.3
  xdotool key --clearmodifiers ctrl+a
  xdotool key --clearmodifiers BackSpace
  xclip -selection primary -loops 3 < "$CHAT_FILE" >/dev/null 2>&1 &
  sleep 0.4
  xdotool mousemove "$SEARCH_X" "$SEARCH_Y" click 2
  sleep 0.6
  xdotool key --clearmodifiers Return
  sleep 1.2
}

defocus
switch_chat

# 2) 点击输入框并清空
xdotool mousemove "$INPUT_X" "$INPUT_Y" click 1
sleep 0.3
xdotool key --clearmodifiers ctrl+a
xdotool key --clearmodifiers BackSpace

# 3) 中文必须走剪贴板 + 中键
xclip -selection primary -loops 3 < "$TEXT_FILE" >/dev/null 2>&1 &
sleep 0.5
xdotool mousemove "$INPUT_X" "$INPUT_Y" click 2
sleep 0.6

# 4) 回车发送
xdotool key --clearmodifiers Return
sleep 0.9

defocus
echo "SENT_REQUESTED"
echo "提示：这只能说明动作发出去了；要确认真的发出去，请回读客户端数据库（见 docs/03）"
rm -rf "$TMP_DIR"
