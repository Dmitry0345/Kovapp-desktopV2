# Ретранслятор для контейнера Appium: 0.0.0.0:15601 → 127.0.0.1:5601 (adb эмулятора).
# Нужен, только если Docker не проводит запросы к localhost этого компьютера.
param([int]$Listen = 15601, [int]$Target = 5601)
$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $Listen)
$listener.Start()
while ($true) {
    $client = $listener.AcceptTcpClient()
    try {
        $server = New-Object System.Net.Sockets.TcpClient("127.0.0.1", $Target)
        $a = $client.GetStream()
        $b = $server.GetStream()
        [void]$a.CopyToAsync($b)
        [void]$b.CopyToAsync($a)
    } catch {
        $client.Close()
    }
}
