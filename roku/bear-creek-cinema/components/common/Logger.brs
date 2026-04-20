' Thin structured-ish logger.
'
' BrightScript has no structlog. We lean on the port-8085 telnet
' debug console (phase5-09 sideload script tails it), so the
' useful bits are: consistent prefix, event-name first, fields
' rendered as key=value pairs. Keeps grepping the tail stream
' sane when multiple scenes are talking at once.

sub logInfo(event as String, fields = invalid as Object)
    print _formatLog("INFO", event, fields)
end sub

sub logWarn(event as String, fields = invalid as Object)
    print _formatLog("WARN", event, fields)
end sub

sub logError(event as String, fields = invalid as Object)
    print _formatLog("ERR ", event, fields)
end sub

function _formatLog(level as String, event as String, fields as Object) as String
    line = "[bcc] " + level + " " + event
    if fields <> invalid then
        for each key in fields
            line = line + " " + key + "=" + _stringify(fields[key])
        end for
    end if
    return line
end function

function _stringify(v as Object) as String
    kind = type(v)
    if kind = "roString" or kind = "String" then return v
    if kind = "roInt" or kind = "Integer" then return v.toStr()
    if kind = "roFloat" or kind = "Float" or kind = "roDouble" or kind = "Double" then return v.toStr()
    if kind = "roBoolean" or kind = "Boolean"
        if v then return "true"
        return "false"
    end if
    if kind = "roInvalid" or kind = "Invalid" then return "invalid"
    return "<" + kind + ">"
end function
