# 1C CLI Tools

This directory contains helper scripts for local 1C:Enterprise Designer validation.

## Test Infobase

Configure your own file or server test infobase. The scripts do not require a hard-coded database.

```text
File="C:\path\to\test-infobase"
```

or:

```text
Srvr="<server-host>";Ref="<infobase-ref>"
```

If the test infobase requires authentication, pass `-UserName` and optionally `-Password`.
If the password is empty, do not pass `-Password`.

Default platform:

```text
C:\Program Files\1cv8\8.3.27.1644\bin
```

## Scripts

- `1c-cf-manage\scripts\cf-validate.ps1` - validate a configuration dump directory or `Configuration.xml`.
- `1c-db-ops\scripts\db-create.ps1` - create the server test infobase when a new test base is needed or the current one is broken.
- `1c-db-ops\scripts\db-load-xml.ps1` - load a dump directory into the server test infobase.
- `1c-db-ops\scripts\db-update.ps1` - run `/UpdateDBCfg` after loading.
- `1c-db-ops\scripts\db-dump-cf.ps1` - dump the loaded configuration to `.cf`.

## Typical Workflow

```powershell
powershell.exe -NoProfile -File ".\tools\1c-cf-manage\scripts\cf-validate.ps1" `
  -ConfigPath "<config_dump_dir>"

powershell.exe -NoProfile -File ".\tools\1c-db-ops\scripts\db-load-xml.ps1" `
  -V8Path "C:\Program Files\1cv8\8.3.27.1644\bin" `
  -InfoBasePath "C:\path\to\test-infobase" `
  -UserName "<user-name>" `
  -ConfigDir "<config_dump_dir>" `
  -Mode Full

powershell.exe -NoProfile -File ".\tools\1c-db-ops\scripts\db-update.ps1" `
  -V8Path "C:\Program Files\1cv8\8.3.27.1644\bin" `
  -InfoBasePath "C:\path\to\test-infobase" `
  -UserName "<user-name>"

powershell.exe -NoProfile -File ".\tools\1c-db-ops\scripts\db-dump-cf.ps1" `
  -V8Path "C:\Program Files\1cv8\8.3.27.1644\bin" `
  -InfoBasePath "C:\path\to\test-infobase" `
  -UserName "<user-name>" `
  -OutputFile "<output_cf_path>"
```

Server example:

```powershell
powershell.exe -NoProfile -File ".\tools\1c-db-ops\scripts\db-load-xml.ps1" `
  -V8Path "C:\Program Files\1cv8\8.3.27.1644\bin" `
  -InfoBaseServer "<server-host>" `
  -InfoBaseRef "<infobase-ref>" `
  -UserName "<user-name>" `
  -ConfigDir "<config_dump_dir>" `
  -Mode Full
```

Use `db-create.ps1` only when a new test infobase is needed or the current one is broken:

```powershell
powershell.exe -NoProfile -File ".\tools\1c-db-ops\scripts\db-create.ps1" `
  -V8Path "C:\Program Files\1cv8\8.3.27.1644\bin" `
  -InfoBaseServer "<server-host>" `
  -InfoBaseRef "<infobase-ref>"
```
