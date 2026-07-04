import type { ReactNode } from "react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Spinner } from "@/components/Spinner";
import { Empty } from "@/components/Empty";
import { MergeSourceBadge, ReachableBadge } from "@/components/vuln-badges";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { FileSystemPicker } from "@/components/FileSystemPicker";

function FileSystemPickerDemo() {
  const [path, setPath] = useState("/");
  return (
    <div className="flex items-center gap-2">
      <FileSystemPicker value={path} onChange={setPath} />
      <span className="font-mono text-sm text-muted-foreground">已选：{path}</span>
    </div>
  );
}

export function DevComponentsPage() {
  return (
    <div className="space-y-8">
      <h1 className="font-serif text-2xl">Component Preview (dev-only)</h1>

      <Section title="Theme">
        <ThemeToggle />
        <span className="text-sm text-muted-foreground">点切换深/浅，刷新验持久化</span>
      </Section>

      <Section title="Buttons">
        <Button>default</Button>
        <Button variant="secondary">secondary</Button>
        <Button variant="ghost">ghost</Button>
        <Button variant="outline">outline</Button>
        <Button variant="destructive">destructive</Button>
        <Button size="sm">small</Button>
        <Button size="icon" aria-label="op">⏵</Button>
      </Section>

      <Section title="Inputs">
        <Label htmlFor="i1">文本</Label>
        <Input id="i1" placeholder="type..." />
        <Textarea placeholder="多行" />
      </Section>

      <Section title="Selection">
        <Checkbox id="c1" defaultChecked />
        <Label htmlFor="c1">勾选</Label>
        <Switch defaultChecked aria-label="开关" />
      </Section>

      <Section title="Badges">
        <Badge>default</Badge>
        <MergeSourceBadge source="llm-only" />
        <MergeSourceBadge source="gitnexus-only" />
        <MergeSourceBadge source="both" />
        <ReachableBadge reachable={true} />
        <ReachableBadge reachable={false} />
      </Section>

      <Section title="Spinner">
        <Spinner label="running" />
        <Spinner />
      </Section>

      <Section title="Empty">
        <div className="w-full">
          <Empty title="no workspaces" hint="新建一个扫描开始">
            <Button>+ new scan</Button>
          </Empty>
        </div>
      </Section>

      <Section title="Skeleton">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-4 w-64" />
      </Section>

      <Section title="Tabs">
        <Tabs defaultValue="a">
          <TabsList>
            <TabsTrigger value="a">Tab A</TabsTrigger>
            <TabsTrigger value="b">Tab B</TabsTrigger>
          </TabsList>
          <TabsContent value="a">content a</TabsContent>
          <TabsContent value="b">content b</TabsContent>
        </Tabs>
      </Section>

      <Section title="Card">
        <Card className="max-w-sm">
          <CardHeader>
            <CardTitle>title</CardTitle>
          </CardHeader>
          <CardContent>content</CardContent>
        </Card>
      </Section>

      <Section title="FileSystemPicker">
        <FileSystemPickerDemo />
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h2 className="font-serif text-lg text-muted-foreground">{title}</h2>
      <div className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-card p-4">
        {children}
      </div>
    </section>
  );
}
