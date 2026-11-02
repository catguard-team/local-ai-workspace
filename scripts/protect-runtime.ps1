$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root '.runtime'
$null = New-Item -ItemType Directory -Path $runtime -Force
$item = Get-Item -LiteralPath $runtime -Force
if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '.runtime must be a regular directory.' }
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$systemSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetOwner($userSid)
$acl.SetAccessRuleProtection($true, $false)
$inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
foreach ($sid in @($userSid, $systemSid)) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, [Security.AccessControl.FileSystemRights]::FullControl, $inheritance, [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow)
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $runtime -AclObject $acl
