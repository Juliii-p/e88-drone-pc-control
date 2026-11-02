# Connect / disconnect ONLY a dedicated USB Wi-Fi adapter to the drone AP.
# The built-in Wi-Fi is left untouched; at the end it verifies internet still goes through it.
# This lets you keep internet on the built-in adapter while the drone has no internet.
#
#   pwsh -File .\e99_net.ps1 -Connect    -Ifc 'Wi-Fi 2' -Ssid 'WIFI-UFO-xxxxxx' [-Mac 'AA-BB-CC-DD-EE-FF']
#   pwsh -File .\e99_net.ps1 -Disconnect -Ifc 'Wi-Fi 2' -Ssid 'WIFI-UFO-xxxxxx'
#
# -Ifc  : the USB adapter name (see `Get-NetAdapter`). Default 'Wi-Fi 2'.
# -Ssid : the drone's open SSID (printed/scan it: WIFI-UFO-xxxxxx).
# -Mac  : optional. If given, the script refuses to touch any adapter whose MAC differs
#         (safety, so it never reconfigures your built-in card by mistake).
param(
    [switch]$Connect,
    [switch]$Disconnect,
    [string]$Ifc = 'Wi-Fi 2',
    [string]$Ssid = 'WIFI-UFO-xxxxxx',
    [string]$Mac = '',
    [int]$Metric = 9000
)

$ErrorActionPreference = 'Stop'

# Setting the interface metric needs admin: relaunch elevated if not already.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $op = if ($Connect) { '-Connect' } else { '-Disconnect' }
    $arg = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', $PSCommandPath, $op,
             '-Ifc', $Ifc, '-Ssid', $Ssid, '-Mac', $Mac, '-Metric', $Metric)
    Start-Process (Get-Command pwsh).Source -Verb RunAs -ArgumentList $arg
    return
}

$a = Get-NetAdapter -Name $Ifc -ErrorAction Stop
if ($Mac -and $a.MacAddress -ne $Mac) { throw "$Ifc is not the expected USB adapter ($($a.MacAddress)). Doing nothing." }

function Disconnect-Drone {
    netsh wlan disconnect interface="$Ifc" | Out-Null
    netsh wlan delete profile name="$Ssid" interface="$Ifc" 2>$null | Out-Null
    Set-NetIPInterface -InterfaceAlias $Ifc -AddressFamily IPv4 -AutomaticMetric Enabled -ErrorAction SilentlyContinue
    "$Ifc disconnected from the drone, metric set back to automatic."
}

if ($Disconnect) { Disconnect-Drone; return }
if (-not $Connect) { 'Use -Connect or -Disconnect'; return }

# If the built-in card were on 192.168.1.x it would clash with the drone: stop.
$builtin = (Get-NetIPAddress -InterfaceAlias 'Wi-Fi' -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress
if ($builtin -like '192.168.1.*') { throw "The built-in card has $builtin, same subnet as the drone. Change your home network first." }

# Open profile, for this adapter only, manual mode (Windows won't auto-connect).
$xml = @"
<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>$Ssid</name>
  <SSIDConfig><SSID><name>$Ssid</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>auto</connectionMode>
  <MSM><security><authEncryption><authentication>open</authentication><encryption>none</encryption><useOneX>false</useOneX></authEncryption></security></MSM>
</WLANProfile>
"@
$tmp = Join-Path $env:TEMP 'e99_profile.xml'
Set-Content -Path $tmp -Value $xml -Encoding utf8
netsh wlan add profile filename="$tmp" interface="$Ifc" user=current | Out-Null
Remove-Item $tmp
netsh wlan connect name="$Ssid" ssid="$Ssid" interface="$Ifc" | Out-Null

# Wait for the drone's DHCP IP.
$ip = $null
foreach ($i in 1..30) {
    Start-Sleep -Milliseconds 700
    $ip = (Get-NetIPAddress -InterfaceAlias $Ifc -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object IPAddress -like '192.168.1.*').IPAddress
    if ($ip) { break }
}
if (-not $ip) { Disconnect-Drone; throw 'No 192.168.1.x IP from the drone. Is it powered on?' }

Set-NetIPInterface -InterfaceAlias $Ifc -AddressFamily IPv4 -InterfaceMetric $Metric

# Verify: internet via the built-in card, drone via the USB adapter.
$viaInternet = (Find-NetRoute -RemoteIPAddress 1.1.1.1 | Select-Object -First 1).InterfaceAlias
$viaDrone = (Find-NetRoute -RemoteIPAddress 192.168.1.1 | Select-Object -First 1).InterfaceAlias
if ($viaInternet -ne 'Wi-Fi' -or $viaDrone -ne $Ifc) {
    Disconnect-Drone
    throw "Unexpected routes (internet via '$viaInternet', drone via '$viaDrone'). Disconnected $Ifc."
}
"OK  $Ifc = $ip (metric $Metric)  | internet still via 'Wi-Fi' ($builtin)  | drone via '$viaDrone'"
"Ping the drone: " + (Test-Connection 192.168.1.1 -Count 2 -Quiet)
