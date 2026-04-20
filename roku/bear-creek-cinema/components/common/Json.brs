' JSON parse + stringify helpers with safe-on-error behavior.
'
' BrightScript has ParseJson and FormatJson built in; these thin
' wrappers log decode failures so a malformed API response
' surfaces in the telnet console instead of silently returning
' invalid.

function parseJsonSafe(body as String, label = "json" as String) as Object
    if body = "" then return invalid
    parsed = ParseJson(body)
    if parsed = invalid then
        print "[bcc] WARN " + label + ".parse_failed body_bytes=" + stri(Len(body))
    end if
    return parsed
end function

function stringifyJsonSafe(obj as Object) as String
    if obj = invalid then return ""
    rendered = FormatJson(obj)
    if rendered = invalid then return ""
    return rendered
end function
