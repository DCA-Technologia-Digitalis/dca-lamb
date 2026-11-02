# Segurança — DV Saúde / DV Odonto

- Nunca versione `.env`, bancos, uploads ou backups.
- Gere `SECRET_KEY` e credenciais próprias, fortes e distintas; substitua os valores de exemplo antes de subir serviços.
- Use HTTPS em toda comunicação externa e restrinja o firewall às portas 80/443.
- PostgreSQL e Redis ficam na rede interna do Docker.
- Anexos clínicos ficam em armazenamento privado e só são entregues por rota autenticada. `/static/uploads/` é bloqueada.
- As rotas clínicas e de farmácia verificam o vínculo do usuário com a unidade CNES.
- Nginx registra o caminho sem query string. Não adicione dados de saúde a URLs ou logs.
- Faça backup e mantenha uma cópia cifrada fora do host; teste a restauração.
- Revise contas administrativas e credenciais periodicamente.
- O Super Admin DCA exige TOTP; segredos ficam cifrados com chave derivada de `SECRET_KEY`, e codigos de recuperacao sao armazenados como hashes. Preserve a mesma chave em todas as replicas e restauracoes.
- Logs devem conter somente metadados tecnicos minimos. Nao registre nomes, documentos, diagnosticos, conteudo clinico, senhas, segredos TOTP ou codigos de recuperacao.
- Ainda nao existe gateway nem webhook de pagamento. Ao integrar, validar assinatura e origem, idempotencia e protecao contra replay.
