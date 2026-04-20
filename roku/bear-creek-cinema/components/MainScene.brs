' MainScene behavior.
'
' Phase5-01 scope: own focus, log the remote events we plan to
' route through later cards, and surface the agentUrl transition
' so a restart-with-no-URL immediately falls into settings once
' phase5-02 lands.

sub init()
    logInfo("MainScene.init")
    m.top.setFocus(true)
    m.top.observeField("agentUrl", "onAgentUrlChanged")
end sub

sub onAgentUrlChanged()
    logInfo("MainScene.onAgentUrlChanged", { agentUrl: m.top.agentUrl })
    if m.top.agentUrl = "" then
        m.top.findNode("statusLabel").text =
            "No agent URL configured. Press * to open settings."
    else
        m.top.findNode("statusLabel").text =
            "Agent: " + m.top.agentUrl
    end if
end sub

' Remote-key handler. Phase5-01 just logs presses so the debug
' console proves the scene is alive; real routing lands with the
' settings / grid / search scenes.
function onKeyEvent(key as String, press as Boolean) as Boolean
    if not press then return false
    logInfo("MainScene.key", { key: key })
    ' * opens settings (phase5-02) when it arrives. For now just
    ' log so the future wiring is obvious.
    return false
end function
