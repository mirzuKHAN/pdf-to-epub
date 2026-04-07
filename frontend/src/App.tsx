import { useState } from "react";
import {
  Container,
  Title,
  Paper,
  Group,
  Text,
  Button,
  TextInput,
  NumberInput,
  SegmentedControl,
  Stack,
  Notification,
  Center,
  ActionIcon,
  useMantineColorScheme,
  Alert,
  List,
  Anchor,
} from "@mantine/core";
import { Dropzone, PDF_MIME_TYPE } from "@mantine/dropzone";
import {
  IconUpload,
  IconFileTypePdf,
  IconX,
  IconCheck,
  IconSun,
  IconMoon,
  IconInfoCircle,
} from "@tabler/icons-react";

export default function App() {
  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const dark = colorScheme === "dark";

  const [file, setFile] = useState<File | null>(null);
  const [token, setToken] = useState<string>("");
  const [parseMode, setParseMode] = useState<string>("mineru-only");
  const [title, setTitle] = useState<string>("Converted Document");
  const [author, setAuthor] = useState<string>("Unknown Author");
  const [coverPageIndex, setCoverPageIndex] = useState<number | string>(0);
  const [skipPages, setSkipPages] = useState<string>("");

  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<boolean>(false);

  const handleConvert = async () => {
    if (!file) {
      setError("Please upload a PDF file.");
      return;
    }
    if (!token) {
      setError("MinerU Token is required.");
      return;
    }

    setIsLoading(true);
    setError(null);
    setSuccess(false);

    const formData = new FormData();
    formData.append("file", file);
    formData.append("token", token);
    formData.append("title", title);
    formData.append("author", author);
    formData.append("cover_page_index", coverPageIndex.toString());
    formData.append("skip_pages", skipPages);

    try {
      const endpoint = `http://127.0.0.1:8000/convert/${parseMode}`;

      const response = await fetch(endpoint, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Server error: ${response.status}`);
      }

      const blob = await response.blob();
      const downloadUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = downloadUrl;
      link.download = `${title.replace(/[^a-z0-9]/gi, "_").toLowerCase() || "converted"}.epub`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(downloadUrl);

      setSuccess(true);
    } catch (err: any) {
      setError(err.message || "An unknown error occurred");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Container size="md" py="xl">
      {/* Header with Title and Theme Toggle */}
      <Group justify="space-between" align="center" mb="xl">
        <Title order={2}>PDF to EPUB Converter</Title>
        <ActionIcon
          variant="outline"
          color={dark ? "yellow" : "blue"}
          onClick={() => toggleColorScheme()}
          title="Toggle color scheme"
          size="lg"
        >
          {dark ? <IconSun size={18} /> : <IconMoon size={18} />}
        </ActionIcon>
      </Group>

      {/* Instructions */}
      <Alert
        icon={<IconInfoCircle size={18} />}
        title="Quick Instructions"
        color="blue"
        mb="md"
        variant="light"
      >
        <List type="ordered" size="sm" spacing="xs">
          <List.Item>
            Register and obtain a MinerU API token at{" "}
            <Anchor href="https://mineru.net" target="_blank" rel="noreferrer">
              https://mineru.net
            </Anchor>
            .
          </List.Item>
          <List.Item>
            Input your token and all required parameters below.
          </List.Item>
          <List.Item>
            Upload your PDF and click Convert. The download will start
            automatically once processing is finished (just wait a little bit!).
          </List.Item>
        </List>
      </Alert>

      <Paper shadow="sm" radius="md" p="xl" withBorder>
        <Stack gap="lg">
          {/* File Upload Zone */}
          <Dropzone
            onDrop={(files) => setFile(files[0])}
            onReject={() =>
              setError("File rejected. Please upload a valid PDF.")
            }
            maxSize={50 * 1024 ** 2} // 50MB
            accept={PDF_MIME_TYPE}
            multiple={false}
            loading={isLoading}
          >
            <Group
              justify="center"
              gap="xl"
              mih={150}
              style={{ pointerEvents: "none" }}
            >
              <Dropzone.Accept>
                <IconUpload
                  size={50}
                  color="var(--mantine-color-blue-6)"
                  stroke={1.5}
                />
              </Dropzone.Accept>
              <Dropzone.Reject>
                <IconX
                  size={50}
                  color="var(--mantine-color-red-6)"
                  stroke={1.5}
                />
              </Dropzone.Reject>
              <Dropzone.Idle>
                <IconFileTypePdf
                  size={50}
                  color="var(--mantine-color-dimmed)"
                  stroke={1.5}
                />
              </Dropzone.Idle>

              <Stack gap={4} align="center">
                <Text size="xl" inline>
                  {file ? file.name : "Drag PDF here or click to select"}
                </Text>
                <Text size="sm" c="dimmed" inline>
                  {file
                    ? `${(file.size / 1024 / 1024).toFixed(2)} MB`
                    : "Attach a single PDF file, up to 50MB"}
                </Text>
              </Stack>
            </Group>
          </Dropzone>

          {/* Configuration Form */}
          <Group grow align="flex-start">
            <Stack gap="sm">
              <Text fw={500} size="sm">
                Parsing Mode
              </Text>
              <SegmentedControl
                value={parseMode}
                onChange={setParseMode}
                data={[
                  { label: "MinerU Only (Fast)", value: "mineru-only" },
                  { label: "Full Parse (MinerU + OLMOCR)", value: "full" },
                ]}
                disabled={isLoading}
              />

              <TextInput
                label="MinerU Token"
                placeholder="eyJhbG..."
                required
                value={token}
                onChange={(e) => setToken(e.currentTarget.value)}
                disabled={isLoading}
              />
              <TextInput
                label="Book Title"
                placeholder="My Awesome Book"
                value={title}
                onChange={(e) => setTitle(e.currentTarget.value)}
                disabled={isLoading}
              />
              <TextInput
                label="Author"
                placeholder="John Doe"
                value={author}
                onChange={(e) => setAuthor(e.currentTarget.value)}
                disabled={isLoading}
              />
            </Stack>

            <Stack gap="sm">
              <NumberInput
                label="Cover Page Index (0-based)"
                min={0}
                value={coverPageIndex}
                onChange={(val) => setCoverPageIndex(val)}
                disabled={isLoading}
              />
              <TextInput
                label="Pages to Skip"
                placeholder="e.g. 0,1,2,10"
                description="Comma-separated indexes to exclude from EPUB"
                value={skipPages}
                onChange={(e) => setSkipPages(e.currentTarget.value)}
                disabled={isLoading}
              />
            </Stack>
          </Group>

          {/* Notifications */}
          {error && (
            <Notification
              icon={<IconX size={18} />}
              color="red"
              onClose={() => setError(null)}
            >
              {error}
            </Notification>
          )}
          {success && (
            <Notification
              icon={<IconCheck size={18} />}
              color="teal"
              onClose={() => setSuccess(false)}
            >
              Successfully converted and downloaded EPUB!
            </Notification>
          )}

          {/* Action Button */}
          <Center mt="md">
            <Button
              size="lg"
              onClick={handleConvert}
              loading={isLoading}
              disabled={!file || !token}
            >
              Convert & Download EPUB
            </Button>
          </Center>
        </Stack>
      </Paper>
    </Container>
  );
}
