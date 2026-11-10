<template>
  <section class="panel markdown">
    <h1>法规知识库导入说明</h1>
    <p>
      容器首次使用空知识库时会自动导入并启用 samples/laws 下全部
      TXT。已有知识库不会被覆盖。
    </p>
    <p>
      若仍提示无依据，请管理员检查容器日志中的自动导入条数与警告，再按以下方式导入。启用表示已确认来源可用于本系统审查。
    </p>
    <pre>
docker compose --env-file .env.docker exec api python -m app.cli import-law --file samples/laws/civil_code_liability.txt --source-id civil_code_liability --name civil_code_liability
docker compose --env-file .env.docker exec api python -m app.cli enable-source --source-id civil_code_liability
docker compose --env-file .env.docker restart api worker</pre
    >
    <p>
      自行提供的法规必须核对文本、适用版本及来源，重启后索引才会更新。更新知识库不会改写既有审查结果，请重新发起审查。
    </p>
    <router-link to="/workspace/contracts">返回合同列表</router-link>
  </section>
</template>
