' Entry point for Bear Creek Cinema.
'
' Main() runs once per launch. It builds the MainScene, hands off
' the persisted agent URL from registry, and parks on the event
' loop until the screen is closed. Everything else — navigation,
' HTTP calls, settings — lives on the scene graph side.

sub Main()
    print "[boot] bear-creek-cinema starting"

    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.setMessagePort(port)

    scene = screen.createScene("MainScene")
    screen.show()

    ' Hand the persisted agent URL to the scene as a starting point.
    ' SettingsScene (phase5-02) writes it back when the user edits
    ' the URL; the scene reads it again on its own whenever it
    ' needs to make a request.
    agentUrl = readAgentUrl()
    scene.agentUrl = agentUrl
    print "[boot] agentUrl = "; agentUrl

    ' Park on the port; exit cleanly on an isScreenClosed event.
    while true
        msg = wait(0, port)
        msgType = type(msg)
        if msgType = "roSGScreenEvent"
            if msg.isScreenClosed() then
                print "[boot] screen closed, exiting"
                return
            end if
        end if
    end while
end sub

' Read the configured agent base URL from per-channel registry.
' Returns "" on a fresh install — MainScene reacts by routing into
' the settings flow (phase5-02).
function readAgentUrl() as String
    section = CreateObject("roRegistrySection", "BearCreekCinema")
    if section.Exists("agentUrl") then
        return section.Read("agentUrl")
    end if
    return ""
end function
