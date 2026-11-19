# proj_waiting_segment <session_id>
# Prints "◆ 等你 N · <project>[ +N-1]" (truecolor orange) from
# ${PROJ_CACHE:-$HOME/.cache/proj}/live/statusline.state, leaving out the
# caller's own session. Prints nothing when the file is missing, broken, or
# its "updated" is more than 120 seconds away from now (either side).
# Always returns 0; safe under `set -euo pipefail`.
# Meant to be sourced by statusline.sh; uses one jq call, no files written.
proj_waiting_segment() {
  local _pw_file _pw_out
  _pw_file="${PROJ_CACHE:-${HOME:-}/.cache/proj}/live/statusline.state"
  _pw_out=$(jq -r --arg me "${1:-}" '
    if (.updated | type) != "number" or (now - .updated) > 120 or (.updated - now) > 120
    then empty
    else
      [(.waiting // [])[] | select(.session_id != $me)] as $w
      | if ($w | length) == 0 then empty
        else "\u001b[38;2;240;163;94m◆ 等你 \($w | length) · \($w[0].project // "" | tostring
                                                   | gsub("[\u0000-\u001f\u007f-\u009f]"; ""))"
             + (if ($w | length) > 1 then " +\($w | length - 1)" else "" end)
             + "\u001b[0m"
        end
    end' "$_pw_file" 2>/dev/null) || _pw_out=""
  if [ -n "$_pw_out" ]; then printf '%s' "$_pw_out"; fi
  return 0
}
