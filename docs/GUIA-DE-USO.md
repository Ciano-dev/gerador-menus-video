# Guia de uso

## Criar e exportar

1. Abra `menu_gui.py` pelo Python ou use o pacote portátil Windows.
2. Adicione de um a seis vídeos. Selecione uma cena para mudar o nome e os campos Início/Fim. Confirme em **Aplicar nome e trecho**.
3. Ajuste título, alinhamento, cores, família e tamanho das fontes. A miniatura é estática e aproximada; não é um player do vídeo final.
4. Escolha a duração do menu. Novos projetos começam com um minuto em duração manual. Confirme alterações em **Aplicar duração**. Adicionar música não troca automaticamente o modo manual; **Usar duração da música** é uma escolha explícita.
5. Escolha MP4 ou AVI e clique em **Exportar vídeo (1080p)…**. Não é obrigatório salvar o projeto antes de exportar.

Ao terminar, **Abrir último vídeo** usa o player padrão do computador e **Abrir pasta do vídeo** localiza o arquivo gerado.

## Música e duração

O áudio das filmagens não entra no menu. A música opcional toca uma vez: se acabar antes do menu, o restante fica em silêncio; se durar mais, termina junto com o menu. A interface avisa sobre essa diferença ao aplicar a duração. Há um fade breve no final do áudio disponível.

Menus com mais de seis minutos exigem confirmação. A barra de andamento representa a etapa atual, não um percentual único de toda a exportação.

## Salvar e continuar depois

**Salvar projeto como…** gera um arquivo `.menu` com referências às mídias. Ele não incorpora vídeos nem música. Mantenha os arquivos nos locais referenciados para reabrir o projeto.

O projeto guarda nomes, trechos, duração, cores, formato, alinhamento e escolhas de fonte. A opção Atual preserva a aparência anterior; oito famílias livres adicionais acompanham o código. A miniatura Tkinter pode diferir das medidas tipográficas do vídeo exportado.

## Preservação dos arquivos

O programa lê as mídias sem mover, renomear ou recortar os originais. Exporta somente o menu escolhido. Não copie sua única versão de uma filmagem para uma pasta de trabalho sem manter backup independente.

Pela interface, nomes de saída ocupados recebem numeração automática. A CLI exige uma saída nova. **Cancelar** interrompe o trabalho e aguarda a limpeza dos temporários próprios antes de liberar outra operação. O cancelamento não apaga os originais nem vídeos já concluídos.

Uma interrupção abrupta, falta de espaço ou falha de permissão pode deixar um arquivo parcial ou staging. Confira o destino antes de limpar arquivos manualmente; não apague filmagens para resolver uma falha de exportação.

## Formatos

- MP4: H.264, yuv420p, 30 fps; música em AAC estéreo quando selecionada.
- AVI: MPEG-4 Part 2/XVID, yuv420p, 30 fps; música em MP3 estéreo quando selecionada.

A existência desses formatos não garante reprodução em qualquer TV. Algumas mídias podem ser recusadas quando o programa não consegue medir a duração de forma confiável.

## Linha de comando

```sh
python menu_anisio.py projeto.menu --saida menu.mp4 --perfil 1080p
python menu_anisio.py projeto.menu --saida prova.mp4 --perfil prova
```

O perfil `prova` é uma rota técnica de baixa resolução, não um player integrado nem um botão de prévia animada da interface. A extensão deve corresponder ao formato salvo no projeto.
