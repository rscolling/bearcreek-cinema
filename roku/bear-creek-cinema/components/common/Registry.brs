' Per-channel registry helpers.
'
' All app state that needs to survive a reboot lives under the
' "BearCreekCinema" section. Keep entries boring — no secrets,
' no large blobs; the Roku is on the LAN but the registry isn't
' encrypted.

function registryRead(key as String, fallback = "" as String) as String
    section = CreateObject("roRegistrySection", "BearCreekCinema")
    if section.Exists(key) then
        return section.Read(key)
    end if
    return fallback
end function

function registryWrite(key as String, value as String) as Boolean
    section = CreateObject("roRegistrySection", "BearCreekCinema")
    ok = section.Write(key, value)
    ' Flush so reads after this one see the updated value even
    ' after a hard reset.
    section.Flush()
    return ok
end function

function registryDelete(key as String) as Boolean
    section = CreateObject("roRegistrySection", "BearCreekCinema")
    if not section.Exists(key) then return true
    ok = section.Delete(key)
    section.Flush()
    return ok
end function
