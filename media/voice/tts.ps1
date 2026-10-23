# Озвучка промо-ролика голосом Microsoft Irina (встроен в Windows)
param([int]$Rate = 1)
Add-Type -AssemblyName System.Speech
$lines = @(
    "Сферум Актив — школьный бот в мессенджере Макс.",
    "Вы даёте задание за минуту, а бот сам отправит его ученикам.",
    "Ученик присылает фото — вы засчитываете ответ одной кнопкой.",
    "Монеты ученики тратят на школьные награды, а классы соревнуются в рейтинге.",
    "Без бумаг и таблиц: уведомления и напоминания бот рассылает сам.",
    "Попробуйте уже сегодня: найдите бота в Максе и выберите роль учителя."
)
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SelectVoice("Microsoft Irina Desktop")
$synth.Rate = $Rate
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(22050, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
for ($i = 0; $i -lt $lines.Count; $i++) {
    $path = Join-Path $dir ("voice_{0}.wav" -f $i)
    $synth.SetOutputToWaveFile($path, $format)
    $synth.Speak($lines[$i])
}
$synth.SetOutputToNull()
$synth.Dispose()
"ok"

