# Tutorial de Instalação e Uso (para iniciantes)

⚠️ **Antes de tudo: isso mexe no "miolo" do processador. Use com muito cuidado** (o próprio app exige que você confirme os riscos no primeiro uso).

## 1. O que você precisa ter

- Um **notebook/computador com Linux** e um processador Intel da linha Haswell-EP (o app foi feito para o Xeon E5-1650 v3 em placa X99).
- **Acesso de administrador (root)** — você vai precisar da senha de administrador para instalar.
- O Python 3 instalado e o pacote gráfico `PySide6`.

## 2. Instalar

1. Copie a pasta do projeto para o seu Linux.
2. Abra o terminal e entre na pasta:
   ```
   cd xtulinus
   ```
3. Rode o instalador como administrador:
   ```
   sudo ./install.sh
   ```
4. Ele cria um grupo de segurança, instala o "motor" (daemon) que fala com o processador, instala o app visual e ativa tudo para iniciar sozinho ao ligar o PC.
5. **Faça login de novo** (deslogar e logar) para o grupo de segurança valer.

Pronto, instalado!

## 3. Abrir o app

Procure o **XTU-Linux** no menu de aplicativos (ou rode `xtu-linux` no terminal) e clique.

Na primeira vez, ele vai mostrar um **aviso de segurança**. Para liberar as ferramentas, marque a caixinha *"I understand the risks"* e clique em **Continue**.

## 4. Usar (abas da janela)

São 5 abas:

- **Status** — só mostra o estado atual (multiplicador do clock, temperatura, etc). Clique em *Read Status* para atualizar. Bom para espiar sem mexer em nada.
- **Advanced Tuning** — aqui é onde você ajusta:
  - Deslize os controles de **turbo** (com quantos "motores" o CPU está rodando).
  - Ajuste os limites de energia **PL1** (longo prazo) e **PL2** (curto prazo) em watts.
  - **Apply**: aplica agora, mas some ao reiniciar.
  - **Apply and Save as Boot Default**: aplica e usa sempre ao ligar.
  - **Reset to Stock**: volta tudo ao padrão de fábrica (o "botão de emergência").
- **Profiles** — salve configurações com nome (ex: "gaming") e aplique/deletar depois. O **Stock Profile** sempre existe e é a forma rápida de voltar ao normal.
- **Monitoring** — gráficos ao vivo de velocidade, temperatura e consumo. Só atualiza enquanto você está olhando essa aba.
- **About & Safety** — o aviso/balanço completo.

## 5. Dica de segurança (leia!)

- Mexa **um pouquinho de cada vez**. Ex.: aumente só o turbo 1-2 núcleos em +1 e teste.
- Fique de olho na **temperatura** na aba Monitoring.
- Se o PC travar ou não ligar direito, use o **Reset to Stock** ou, em último caso, reinicie — os valores por padrão não "queimam" nada, mas salvem sua configuração de fábrica.
- O app tem travas de segurança (limites máximos de turbo e energia), mas isso **não garante** que não possa dar problema.
