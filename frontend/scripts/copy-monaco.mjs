// Копирует Monaco из node_modules в public/monaco, чтобы редактор не зависел от CDN
// (см. src/components/code/monaco-setup.ts). Каталог public/monaco в git не хранится.
// Языковые сервисы TS/CSS/HTML/JSON и их воркеры не нужны: редактор показывает только Java.
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const pkg = join(root, "node_modules", "monaco-editor");
const source = join(pkg, "min", "vs");
const target = join(root, "public", "monaco", "vs");
const stamp = join(target, ".version");

const version = JSON.parse(readFileSync(join(pkg, "package.json"), "utf8")).version;
if (existsSync(stamp) && readFileSync(stamp, "utf8") === version) process.exit(0);

const skip = (path) => {
  const rel = relative(source, path).split(sep).join("/");
  return rel.startsWith("language/") || /^assets\/(ts|css|html|json)\.worker/.test(rel);
};

rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
cpSync(source, target, { recursive: true, filter: (src) => !skip(src) });
writeFileSync(stamp, version);
console.log(`monaco-editor ${version} → public/monaco/vs`);
