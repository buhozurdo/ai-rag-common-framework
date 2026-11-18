import os
import re
import shutil
import subprocess

source_dir = ".wiki"
wiki_git_dir = ".wiki_git"

if not os.path.exists(wiki_git_dir):
    raise SystemExit(f"Directory {wiki_git_dir} does not exist. Please clone the wiki repo first.")

# Configure remote to SSH
subprocess.run(
    ["git", "remote", "set-url", "origin", "git@github.com:buhozurdo/ai-rag-common-framework.wiki.git"],
    cwd=wiki_git_dir,
    check=True
)

link_pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')

file_mapping = {}
slug_lookup = {}

# Root mappings
file_mapping[os.path.join(source_dir, "Home.md")] = "Home.md"
slug_lookup["Home.md"] = "Home"
slug_lookup["Home"] = "Home"
slug_lookup["README.md"] = "Home"
slug_lookup["README"] = "Home"
slug_lookup["API-CHEATSHEET.md"] = "en-API-CHEATSHEET"
slug_lookup["API-CHEATSHEET"] = "en-API-CHEATSHEET"

for lang in ["en", "es"]:
    lang_dir = os.path.join(source_dir, lang)
    for root, dirs, files in os.walk(lang_dir):
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            full = os.path.join(root, f)
            rel_to_lang = os.path.relpath(full, lang_dir).replace("\\", "/")
            rel_to_source = os.path.relpath(full, source_dir).replace("\\", "/")
            parts = rel_to_lang.split("/")
            
            if len(parts) == 1:
                base = os.path.splitext(f)[0]
                if f in ["Home.md", "README.md"]:
                    target_name = f"{lang}-Home.md"
                    slug = f"{lang}-Home"
                elif f == "INDEX.md":
                    target_name = f"{lang}-INDEX.md"
                    slug = f"{lang}-INDEX"
                elif f == "API-CHEATSHEET.md":
                    target_name = f"{lang}-API-CHEATSHEET.md"
                    slug = f"{lang}-API-CHEATSHEET"
                elif f == "_Sidebar.md":
                    continue
            else:
                sec_num = parts[0].split("-")[0] # '01', '02', etc.
                base = os.path.splitext(f)[0]
                target_name = f"{lang}-{sec_num}-{base}.md"
                slug = f"{lang}-{sec_num}-{base}"
                
            file_mapping[full] = target_name
            
            # Index all lookup patterns
            slug_lookup[rel_to_source] = slug
            slug_lookup[rel_to_source.replace(".md", "")] = slug
            slug_lookup[f"{lang}:{rel_to_lang}"] = slug
            slug_lookup[f"{lang}:{rel_to_lang.replace('.md', '')}"] = slug
            slug_lookup[f"{lang}:{f}"] = slug
            slug_lookup[f"{lang}:{os.path.splitext(f)[0]}"] = slug

def resolve_target(target, context_lang):
    anchor = ""
    if "#" in target:
        parts = target.split("#", 1)
        target = parts[0]
        anchor = "#" + parts[1]
        
    if not target:
        return anchor
        
    if target.startswith("http://") or target.startswith("https://") or target.startswith("mailto:"):
        return target + anchor
        
    found_slug = None
    if target in slug_lookup:
        found_slug = slug_lookup[target]
    elif context_lang and f"{context_lang}:{target}" in slug_lookup:
        found_slug = slug_lookup[f"{context_lang}:{target}"]
    elif target.lstrip("./") in slug_lookup:
        found_slug = slug_lookup[target.lstrip("./")]
    elif context_lang and f"{context_lang}:{target.lstrip('./')}" in slug_lookup:
        found_slug = slug_lookup[f"{context_lang}:{target.lstrip('./')}"]
        
    if found_slug:
        return found_slug + anchor
    else:
        # Fallback sanitization
        clean = target.replace(".md", "").replace("/", "-")
        return clean + anchor

def transform_content(content, context_lang):
    def replace_link(match):
        text = match.group(1)
        target = match.group(2)
        new_target = resolve_target(target, context_lang)
        return f"[{text}]({new_target})"
    return link_pattern.sub(replace_link, content)

# 1. Clean out existing non-.git contents in .wiki_git
for item in os.listdir(wiki_git_dir):
    if item == ".git":
        continue
    item_path = os.path.join(wiki_git_dir, item)
    if os.path.isdir(item_path):
        shutil.rmtree(item_path)
    else:
        os.remove(item_path)

print("Cleaned .wiki_git working directory.")

# 2. Write all transformed markdown files
written = 0
for src_path, target_filename in file_mapping.items():
    rel = os.path.relpath(src_path, source_dir).replace("\\", "/")
    context_lang = "en" if rel.startswith("en/") else ("es" if rel.startswith("es/") else None)
    
    with open(src_path, "r", encoding="utf-8") as fp:
        content = fp.read()
        
    new_content = transform_content(content, context_lang)
    dest_path = os.path.join(wiki_git_dir, target_filename)
    
    with open(dest_path, "w", encoding="utf-8") as fp:
        fp.write(new_content)
    written += 1

print(f"Written {written} transformed markdown files to .wiki_git.")

# 3. Write _Sidebar.md
sidebar_content = """### [AI RAG Wiki](Home)
**[English Documentation](en-Home)** | **[Documentación en Español](es-Home)**

---

### English Documentation (EN)
* **Overview**
  * [English Home](en-Home)
  * [Master Index](en-INDEX)
  * [API Cheat Sheet](en-API-CHEATSHEET)
* **01. Architecture & Concepts**
  * [01. System Philosophy & Trade-offs](en-01-01-system-philosophy-and-design)
  * [02. Design Patterns & Clean RAG](en-01-02-design-patterns-in-rag-and-agents)
  * [03. Data Anatomy & Lifecycle](en-01-03-data-anatomy-and-lifecycle)
  * [04. Multi-Agent Topologies](en-01-04-multi-agent-systems-and-orchestration-topologies)
* **02. Getting Started**
  * [01. Quickstart: First RAG](en-02-01-quickstart-your-first-rag)
  * [02. Setup & Configuration](en-02-02-installation-configuration-and-environments)
  * [03. CLI Interactive Guide](en-02-03-cli-interactive-guide-and-playground)
  * [04. Key Concepts & Glossary](en-02-04-key-concepts-glossary-and-mental-models)
* **03. Implementation & Components**
  * [01. LLM Engine & Resilience](en-03-01-llm-engine-resilience-and-caching)
  * [02. Advanced Hybrid Search & RRF](en-03-02-advanced-rag-hybrid-search-and-reranking)
  * [03. Autonomous Agents & StateGraph](en-03-03-autonomous-agents-and-stategraph)
  * [04. MCP 2.x Protocols](en-03-04-model-context-protocol-mcp)
  * [05. Security Guardrails & Sandbox](en-03-05-security-guardrails-and-sandboxing)
  * [06. FastAPI Gateway & Routes](en-03-06-fastapi-api-gateway-and-routes)
  * [07. Dual Memory Systems](en-03-07-conversational-and-vector-memory)
  * [08. Namespaces & Multi-Tenancy](en-03-08-collections-and-multitenancy)
* **04. Patterns & Recipes**
  * [01. Self-Healing & FinOps](en-04-01-practical-and-advanced-recipes)
  * [02. Testing & Offline Evals](en-04-02-testing-mocking-and-cicd)
  * [03. Migration from LangChain](en-04-03-migration-from-langchain-and-llamaindex)
* **05. Staff Guide & Production**
  * [01. SLAs, Observability & FinOps](en-05-01-enterprise-architecture-slas-observability-and-finops)
  * [02. Incident Runbook & Debugging](en-05-02-troubleshooting-and-production-runbook)

---

### Documentación en Español (ES)
* **General**
  * [Portada en Español](es-Home)
  * [Índice Maestro](es-INDEX)
  * [Hoja de Referencia API](es-API-CHEATSHEET)
* **01. Arquitectura y Conceptos**
  * [01. Filosofía del Sistema](es-01-01-filosofia-y-diseno-del-sistema)
  * [02. Patrones y Clean RAG](es-01-02-patrones-de-diseno-en-sistemas-rag-y-agentes)
  * [03. Anatomía del Dato](es-01-03-anatomia-y-ciclo-de-vida-de-datos)
  * [04. Topologías Multi-Agente](es-01-04-sistemas-multi-agente-y-topologias-de-orquestacion)
* **02. Primeros Pasos**
  * [01. Quickstart: Primer RAG](es-02-01-quickstart-tu-primer-rag)
  * [02. Instalación y Entornos](es-02-02-instalacion-configuracion-y-entornos)
  * [03. Guía Interactiva CLI](es-02-03-guia-interactiva-cli-y-playground)
  * [04. Conceptos y Glosario](es-02-04-conceptos-clave-glosario-y-modelos-mentales)
* **03. Implementación y Componentes**
  * [01. Motor LLM y Caché](es-03-01-motor-llm-resiliencia-y-cache)
  * [02. Búsqueda Híbrida y RRF](es-03-02-rag-avanzado-recuperacion-hibrida-y-reranking)
  * [03. Agentes y StateGraph](es-03-03-agentes-autonomos-y-stategraph)
  * [04. Protocolo MCP 2.x](es-03-04-mcp-model-context-protocol)
  * [05. Seguridad y Guardrails](es-03-05-seguridad-guardrails-y-sandboxing)
  * [06. Gateway FastAPI y Rutas](es-03-06-api-gateway-fastapi-y-rutas)
  * [07. Memoria Dual y Vectores](es-03-07-memoria-conversacional-y-vectorial)
  * [08. Namespaces y Multi-Tenancy](es-03-08-colecciones-y-multitenancy)
* **04. Patrones y Recetas**
  * [01. Auto-Recuperación y FinOps](es-04-01-recetas-practicas-y-avanzadas)
  * [02. Testing y Evals Offline](es-04-02-testing-mocking-y-ci-cd)
  * [03. Migración desde LangChain](es-04-03-migracion-desde-langchain-y-llamaindex)
* **05. Guía Staff y Producción**
  * [01. SLAs, Observabilidad y Costos](es-05-01-arquitectura-empresarial-sla-observabilidad-y-costos)
  * [02. Runbook y Resolución de Errores](es-05-02-troubleshooting-y-runbook-de-errores)
"""

with open(os.path.join(wiki_git_dir, "_Sidebar.md"), "w", encoding="utf-8") as fp:
    fp.write(sidebar_content)

# 4. Write _Footer.md
footer_content = """---
**[AI RAG Common Framework](Home)** | [English Documentation](en-Home) | [Documentación en Español](es-Home) | [Repository](https://github.com/buhozurdo/ai-rag-common-framework)
"""

with open(os.path.join(wiki_git_dir, "_Footer.md"), "w", encoding="utf-8") as fp:
    fp.write(footer_content)

print("Generated _Sidebar.md and _Footer.md.")

# 5. Git commit and push
subprocess.run(["git", "add", "-A"], cwd=wiki_git_dir, check=True)
status_output = subprocess.run(["git", "status", "--porcelain"], cwd=wiki_git_dir, capture_output=True, text=True, check=True)

if status_output.stdout.strip():
    print("Changes detected, committing...")
    subprocess.run(
        ["git", "commit", "-m", "fix(wiki): flatten hierarchy to root pages and remove .md extensions for native GitHub Wiki navigation"],
        cwd=wiki_git_dir,
        check=True
    )
    print("Pushing to GitHub Wiki remote...")
    subprocess.run(["git", "push", "origin", "master"], cwd=wiki_git_dir, check=True)
    print("Successfully pushed to GitHub Wiki!")
else:
    print("No changes to commit in .wiki_git.")
