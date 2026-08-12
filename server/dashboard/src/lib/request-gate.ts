export class RequestGate {
  private generation = 0;
  private pending = false;
  private active = true;

  get isPending() {
    return this.pending;
  }

  begin(): number | null {
    if (!this.active || this.pending) return null;
    this.pending = true;
    this.generation += 1;
    return this.generation;
  }

  invalidate() {
    this.generation += 1;
    this.pending = false;
  }

  dispose() {
    this.active = false;
    this.invalidate();
  }

  isCurrent(request: number) {
    return request === this.generation;
  }

  finish(request: number) {
    if (this.isCurrent(request)) this.pending = false;
  }

  successDisposition(request: number) {
    return {
      revealSecret: this.active && this.isCurrent(request),
      refresh: this.active,
    };
  }
}
