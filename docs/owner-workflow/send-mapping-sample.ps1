# Run locally only after Matthew approves one mapping-only request in the SSP Draft.
# The private inbound URL is entered invisibly and is never saved or printed.
$ErrorActionPreference = 'Stop'
$sampleFile = Join-Path $PSScriptRoot 'mapping-sample.json'
$secretInput = Read-Host 'Paste the SSP QA inbound URL here (hidden; do not paste it into chat)' -AsSecureString
$secretPointer = [IntPtr]::Zero
try {
    $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secretInput)
    $targetUri = [Uri][Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
    if ($targetUri.Scheme -ne 'https' -or $targetUri.Host -ne 'services.leadconnectorhq.com' -or
        $targetUri.AbsolutePath -notmatch '^/hooks/ZnckuEDPIcWu8fn72ppi/webhook-trigger/' -or
        $targetUri.UserInfo -or $targetUri.Query -or $targetUri.Fragment) {
        throw 'Not the expected SSP inbound destination.'
    }
    $sampleBody = Get-Content -LiteralPath $sampleFile -Raw
    $sample = $sampleBody | ConvertFrom-Json
    if ($sample.mode -ne 'mapping' -or $sample.claim_credential -or $sample.completion_credential) {
        throw 'The fixture must remain a credential-free mapping sample.'
    }
    $null = Invoke-RestMethod -Uri $targetUri -Method Post -ContentType 'application/json' -Body $sampleBody -MaximumRedirection 0 -TimeoutSec 30
    Write-Host 'Mapping request accepted. In GHL, fetch the sample, select it, and save the trigger. No email was requested.'
} catch {
    Write-Host 'Mapping request could not be confirmed. Check the destination and GHL sample list. Do not share the URL or detailed error output.'
} finally {
    if ($secretPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer) }
    $targetUri = $null
    $secretInput.Dispose()
}
